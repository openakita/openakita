# src/openakita/agents/cli_runner.py
"""External-CLI subprocess lifecycle layer.

Owns: spawning, tracking, and terminating the CLI subprocess for one
`ExternalCliAgent` turn. Does NOT own argv building, stream parsing, or
resume-id tracking — those belong to the `ProviderAdapter` (plan 08) and
to `ExternalCliAgent` (plan 09) respectively.

Escalation: `terminate_and_wait()` walks SIGINT → SIGTERM → SIGKILL with
bounded grace. Worst case 6s. Constants are module-level so tests can
monkey-patch them to zero.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openakita.agents.cli_providers import ProviderAdapter

# --- cancellation timeouts (named, not magic) ------------------------------
_SIGINT_GRACE_S = 3.0
_SIGTERM_GRACE_S = 2.0
_SIGKILL_GRACE_S = 1.0

# --- concurrency cap --------------------------------------------------------
DEFAULT_MAX_CONCURRENT_EXTERNAL_CLIS = 3  # settings key: external_cli_max_concurrent


class ExitReason(StrEnum):
    COMPLETED = "completed"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CliRunRequest:
    """Per-turn invocation bundle. Immutable so adapters can't stash it."""

    message: str
    resume_id: str | None
    profile: Any
    cwd: Path
    cancelled: asyncio.Event
    session: Any | None
    system_prompt_extra: str
    images: tuple[Path, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    on_progress: Callable[..., Awaitable[None]] | None = None


@dataclass(frozen=True)
class ProviderRunResult:
    """Per-turn outcome bundle. Everything the agent or UI needs, nothing more."""

    final_text: str
    tools_used: list[str]
    artifacts: list[str]
    session_id: str | None
    input_tokens: int
    output_tokens: int
    exit_reason: ExitReason
    errored: bool
    error_message: str | None


class ExternalCliLimiter:
    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT_EXTERNAL_CLIS) -> None:
        self._max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max(1, max_concurrent))

    async def __aenter__(self) -> None:
        await self._sem.acquire()

    async def __aexit__(self, *_exc) -> None:
        self._sem.release()


class SubprocessRunner:
    def __init__(
        self,
        adapter: ProviderAdapter,
        limiter: ExternalCliLimiter,
    ) -> None:
        self._adapter = adapter
        self._limiter = limiter
        self._proc: asyncio.subprocess.Process | None = None

    async def run(self, request: CliRunRequest) -> ProviderRunResult:
        async with self._limiter:
            argv = self._adapter.build_argv(request)
            env = self._adapter.build_env(request)
            return await self._adapter.run(request, argv, env, on_spawn=self._track_proc)

    def _track_proc(self, proc: Any) -> None:
        self._proc = proc

    async def terminate_and_wait(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        for kind, grace in (
            ("SIGINT", _SIGINT_GRACE_S),
            ("SIGTERM", _SIGTERM_GRACE_S),
            ("SIGKILL", _SIGKILL_GRACE_S),
        ):
            try:
                if kind == "SIGINT":
                    proc.send_signal(signal.SIGINT)
                elif kind == "SIGTERM":
                    proc.terminate()
                else:
                    proc.kill()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace)
                return
            except TimeoutError:  # NOT asyncio.TimeoutError — ruff UP041 prefers bare TimeoutError
                continue
