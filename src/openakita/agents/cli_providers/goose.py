# src/openakita/agents/cli_providers/goose.py
"""Goose provider adapter.

Goose is the local-LLM CLI from Block (`block/goose`). Non-interactive mode:
    goose session exec --stream "<message>"
emits JSON-per-line events on stdout. Resume uses `--name <id> --resume`.

No MCP injection path today: Goose reads its own `extensions.toml`; per-turn
override via env is possible but not part of the Phase 2 cut — revisit if
MCP wiring becomes necessary.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_providers._common import (
    binary_not_found_error,
    build_cli_env,
    stream_cli_subprocess,
)
from openakita.agents.cli_runner import (
    CliRunRequest,
    ExitReason,
    ProviderRunResult,
)
from openakita.tools.errors import classify_cli_error
from openakita.utils.path_helper import which_command

logger = logging.getLogger(__name__)

SESSION_ROOT: Path = Path.home() / ".local" / "share" / "goose" / "sessions"
CLI_PROVIDER_ID: CliProviderId = CliProviderId.GOOSE


def _resolve_binary() -> str | None:
    """Return absolute path to `goose` or None if missing. Wrapped so tests can patch."""
    return which_command("goose")


@dataclass(frozen=True)
class _StreamEvent:
    kind: str                           # "init" | "assistant_text" | "tool_use" | "result" | "error"
    session_id: str | None = None
    text: str = ""
    tool_name: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error_message: str | None = None


def _parse_stream_line(line: bytes) -> _StreamEvent | None:
    """Parse one JSONL line from `goose session exec --stream`.

    Returns None for blank lines and non-JSON garbage.
    Unknown event shapes also return None so the parser is future-proof.
    """
    if not line or not line.strip():
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None

    evt = obj.get("event")
    if evt == "session_start":
        return _StreamEvent(kind="init", session_id=obj.get("session_id"))
    if evt == "turn_end":
        usage = obj.get("usage") or {}
        return _StreamEvent(
            kind="result",
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )
    if evt == "error":
        return _StreamEvent(kind="error", error_message=str(obj.get("message", "error")))

    role = obj.get("role")
    if role == "assistant":
        return _StreamEvent(kind="assistant_text", text=str(obj.get("content", "")))
    if role == "tool":
        return _StreamEvent(kind="tool_use", tool_name=str(obj.get("name", "")))
    return None


@dataclass
class _TurnAccumulator:
    session_id: str | None = None
    text_parts: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    errored: bool = False
    error_message: str | None = None

    def apply(self, ev: _StreamEvent) -> None:
        if ev.kind == "init":
            self.session_id = ev.session_id
        elif ev.kind == "assistant_text":
            self.text_parts.append(ev.text)
        elif ev.kind == "tool_use" and ev.tool_name:
            self.tools_used.append(ev.tool_name)
        elif ev.kind == "result":
            self.input_tokens = ev.input_tokens
            self.output_tokens = ev.output_tokens
        elif ev.kind == "error":
            self.errored = True
            self.error_message = ev.error_message


def _cancelled_result(acc: _TurnAccumulator) -> ProviderRunResult:
    return ProviderRunResult(
        final_text="".join(acc.text_parts),
        tools_used=list(acc.tools_used),
        artifacts=[],
        session_id=acc.session_id,
        input_tokens=acc.input_tokens,
        output_tokens=acc.output_tokens,
        exit_reason=ExitReason.CANCELLED,
        errored=False,
        error_message=None,
    )


class GooseAdapter:
    """Goose CLI adapter. Stateless across calls — per-turn state lives in run()."""

    def build_argv(self, request: CliRunRequest) -> list[str]:
        binary = _resolve_binary()
        if binary is None:
            raise binary_not_found_error(
                tool_name="goose",
                binary="goose",
                install_hint="curl -fsSL https://github.com/block/goose/releases/latest/download/goose-installer.sh | bash",
            )
        argv = [binary, "session", "exec", "--stream"]
        if request.resume_id:
            argv += ["--name", request.resume_id, "--resume"]
        argv.append(request.message)
        return argv

    def build_env(self, request: CliRunRequest) -> dict[str, str]:
        return build_cli_env(request.profile)

    async def run(
        self,
        request: CliRunRequest,
        argv: list[str],
        env: dict[str, str],
        *,
        on_spawn: Callable[[asyncio.subprocess.Process], None],
    ) -> ProviderRunResult:
        acc = _TurnAccumulator()
        stderr_buffer: list[bytes] = []
        proc_ref: dict[str, asyncio.subprocess.Process] = {}
        progress_cancelled = False

        def track(proc: asyncio.subprocess.Process) -> None:
            proc_ref["p"] = proc
            on_spawn(proc)

        async def emit_progress(ev: _StreamEvent) -> None:
            nonlocal progress_cancelled
            cb = request.on_progress
            if cb is None:
                return
            try:
                if ev.kind == "assistant_text" and ev.text:
                    await cb("assistant_text", text=ev.text)
                    return
                if ev.kind == "tool_use" and ev.tool_name:
                    await cb("tool_use", tool_name=ev.tool_name)
            except asyncio.CancelledError:
                progress_cancelled = True
                raise
            except Exception:
                logger.debug("progress callback failed", exc_info=True)

        try:
            async for line in stream_cli_subprocess(
                argv,
                env,
                request.cwd,
                request.cancelled,
                on_spawn=track,
                on_stderr=stderr_buffer.append,
            ):
                ev = _parse_stream_line(line)
                if ev is not None:
                    acc.apply(ev)
                    await emit_progress(ev)
        except asyncio.CancelledError:
            if progress_cancelled:
                raise
            return _cancelled_result(acc)

        if request.cancelled.is_set():
            return _cancelled_result(acc)

        proc = proc_ref.get("p")
        exit_code = 0
        if proc is not None:
            try:
                exit_code = await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                exit_code = -1
        stderr_text = b"".join(stderr_buffer).decode("utf-8", "replace")

        if acc.errored or exit_code != 0:
            err_type = classify_cli_error(
                exit_code=exit_code, stderr=stderr_text, exception=None,
            )
            return ProviderRunResult(
                final_text="".join(acc.text_parts),
                tools_used=list(acc.tools_used),
                artifacts=[],
                session_id=acc.session_id,
                input_tokens=acc.input_tokens,
                output_tokens=acc.output_tokens,
                exit_reason=ExitReason.ERROR,
                errored=True,
                error_message=acc.error_message or f"{err_type.value}: {stderr_text[:200]}",
            )

        return ProviderRunResult(
            final_text="".join(acc.text_parts),
            tools_used=list(acc.tools_used),
            artifacts=[],
            session_id=acc.session_id,
            input_tokens=acc.input_tokens,
            output_tokens=acc.output_tokens,
            exit_reason=ExitReason.COMPLETED,
            errored=False,
            error_message=None,
        )

    async def cleanup(self) -> None:
        return None


PROVIDER: GooseAdapter = GooseAdapter()
