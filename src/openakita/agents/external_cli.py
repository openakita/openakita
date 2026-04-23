# src/openakita/agents/external_cli.py
"""Duck-typed Agent substitute for external CLIs.

Owns:
- `AgentState` / `TaskState` — so orchestrator's `_get_progress_fingerprint`
  reads unchanged.
- Resume id (`last_session_id`) — the external CLI's own session handle.
- Prompt composition — first-turn vs. resume branching lives here, not in the
  9 adapters.
- Cancellation plumbing — sets `_cancelled` and awaits subprocess exit.

Does NOT own:
- Argv building / stream parsing — those belong to `ProviderAdapter` (plan 08).
- Subprocess lifecycle — that belongs to `SubprocessRunner` (plan 07).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openakita.agents.cli_providers import ProviderAdapter  # noqa: F401 (triggers _autoload)
from openakita.agents.cli_runner import (
    CliRunRequest,
    ExitReason,
    ExternalCliLimiter,
    ProviderRunResult,
    SubprocessRunner,
)
from openakita.agents.profile import AgentProfile
from openakita.core.agent_state import AgentState, TaskStatus

logger = logging.getLogger(__name__)


class _NullBrain:
    """No-op Brain. The pool's `_find_parent_brain` walk-up must not crash on
    EXTERNAL_CLI agents; this singleton makes `agent.brain` always safe."""

    def append_user(self, *_a: Any, **_kw: Any) -> None:
        pass

    def append_assistant(self, *_a: Any, **_kw: Any) -> None:
        pass

    def append_tool_result(self, *_a: Any, **_kw: Any) -> None:
        pass

    def is_loaded(self) -> bool:
        return False


_NULL_BRAIN = _NullBrain()


@dataclass
class _TurnOutcome:
    text: str
    tools_used: list[str]
    artifacts: list[str]
    elapsed_s: float
    exit_reason: ExitReason
    error: str | None = None


class ExternalCliAgent:
    """Single-turn wrapper around a CLI subprocess.

    Reused across turns of the same session: `_turn` counts how many turns
    this instance has run, `last_session_id` carries the CLI-provided resume
    handle from turn N to turn N+1.
    """

    def __init__(
        self,
        profile: AgentProfile,
        adapter: ProviderAdapter,
        mcp_servers_filtered: list[str] | None = None,
        *,
        limiter: ExternalCliLimiter,
    ) -> None:
        self.profile = profile
        self._adapter = adapter
        self._mcp_servers: tuple[str, ...] = tuple(mcp_servers_filtered or ())
        self._runner = SubprocessRunner(adapter, limiter)
        self._cancelled = asyncio.Event()
        self.agent_state = AgentState()
        self._turn = 0
        self.last_session_id: str | None = None
        # Tolerated but ignored — set by AgentFactory
        self._preferred_endpoint: str | None = None
        self._custom_prompt_suffix: str | None = None

    @property
    def brain(self) -> _NullBrain:
        return _NULL_BRAIN

    async def initialize(self, *, lightweight: bool = True) -> None:
        """No-op — external process has no warm-up. Kwarg name matches native
        `Agent.initialize(lightweight, start_scheduler)` so AgentFactory call
        sites are uniform."""
        return None

    # ---- Orchestrator-facing surface ------------------------------------

    async def chat_with_session(
        self,
        session: Any,
        message: str,
        *,
        is_sub_agent: bool = False,
        image_paths: tuple[Path, ...] = (),
        **_: Any,
    ) -> Any:
        """One CLI turn. Returns a DelegationResult-shaped dict (real type is
        imported by callers; we avoid the import here to keep the module cycle-free)."""
        outcome = await self._run_one_turn(message, session=session, images=image_paths)
        return self._build_delegation_result(outcome)

    async def execute_task_from_message(self, message: str) -> Any:
        """Non-interactive path (scheduler). Same code path as chat_with_session
        but with `session=None` so the adapter skips SSE emission."""
        outcome = await self._run_one_turn(message, session=None, images=())
        return {
            "success": outcome.exit_reason == ExitReason.COMPLETED,
            "data": outcome.text,
            "error": outcome.error,
            "iterations": 1,
            "duration_seconds": outcome.elapsed_s,
        }

    # ---- Lifecycle ------------------------------------------------------

    async def cancel(self) -> None:
        self._cancelled.set()
        await self._runner.terminate_and_wait()

    async def shutdown(self) -> None:
        await self.cancel()
        try:
            await self._adapter.cleanup()
        except Exception:
            # cleanup is a safety hook; don't let a stale temp file crash shutdown
            logger.debug("adapter cleanup failed", exc_info=True)

    # ---- Internals ------------------------------------------------------

    async def _handle_progress(
        self,
        kind: str,
        *,
        text: str = "",
        tool_name: str | None = None,
    ) -> None:
        task = self.agent_state.current_task
        if task is None:
            return
        if self._cancelled.is_set() or task.cancelled or task.is_terminal:
            return
        if kind == "tool_use" and tool_name:
            if task.status != TaskStatus.ACTING:
                try:
                    task.transition(TaskStatus.ACTING)
                except ValueError:
                    logger.debug(
                        "ignoring external CLI tool progress for task status %s",
                        task.status.value,
                    )
                    return
            task.record_tool_execution([tool_name])
            return
        if kind == "assistant_text" and text:
            if task.status != TaskStatus.REASONING:
                try:
                    task.transition(TaskStatus.REASONING)
                except ValueError:
                    logger.debug(
                        "ignoring external CLI assistant progress for task status %s",
                        task.status.value,
                    )
            return

    async def _run_one_turn(
        self,
        message: str,
        *,
        session: Any | None,
        images: tuple[Path, ...],
    ) -> _TurnOutcome:
        self._cancelled.clear()
        start = time.monotonic()
        session_id = getattr(session, "id", "") or getattr(session, "session_id", "")
        conversation_id = getattr(session, "conversation_id", "") if session is not None else ""
        task = self.agent_state.begin_task(
            session_id=session_id,
            conversation_id=conversation_id,
        )
        task.transition(TaskStatus.REASONING)
        self._turn += 1
        task.iteration = self._turn

        prompt, system_extra = self._compose_prompt(message)
        cwd = Path(getattr(session, "cwd", None) or Path.cwd())

        text = ""
        tools_used: list[str] = []
        artifacts: list[str] = []
        error: str | None = None
        exit_reason = ExitReason.COMPLETED

        try:
            request = CliRunRequest(
                message=prompt,
                resume_id=self.last_session_id,
                profile=self.profile,
                cwd=cwd,
                cancelled=self._cancelled,
                session=session,
                system_prompt_extra=system_extra,
                images=tuple(images),
                mcp_servers=self._mcp_servers,
                on_progress=self._handle_progress,
            )
            result: ProviderRunResult = await self._runner.run(request)
            text, tools_used, artifacts = (
                result.final_text,
                list(result.tools_used),
                list(result.artifacts),
            )
            if (
                result.exit_reason == ExitReason.CANCELLED
                or self._cancelled.is_set()
                or task.cancelled
                or task.status == TaskStatus.CANCELLED
            ):
                exit_reason = ExitReason.CANCELLED
                error = result.error_message
                task.cancel("external CLI turn cancelled")
                await self.cancel()
                return _TurnOutcome(
                    text=text,
                    tools_used=tools_used,
                    artifacts=artifacts,
                    elapsed_s=time.monotonic() - start,
                    exit_reason=exit_reason,
                    error=error,
                )
            recorded_tools = list(task.tools_executed)
            unrecorded_tools = []
            for tool_name in tools_used:
                if tool_name in recorded_tools:
                    recorded_tools.remove(tool_name)
                else:
                    unrecorded_tools.append(tool_name)
            task.record_tool_execution(unrecorded_tools)
            if task.status == TaskStatus.ACTING:
                try:
                    task.transition(TaskStatus.OBSERVING)
                    task.transition(TaskStatus.REASONING)
                except ValueError:
                    logger.debug(
                        "leaving external CLI task in status %s after final progress normalization",
                        task.status.value,
                    )
            if self._cancelled.is_set() or task.cancelled or task.status == TaskStatus.CANCELLED:
                exit_reason = ExitReason.CANCELLED
                error = result.error_message
                task.cancel("external CLI turn cancelled")
                await self.cancel()
                return _TurnOutcome(
                    text=text,
                    tools_used=tools_used,
                    artifacts=artifacts,
                    elapsed_s=time.monotonic() - start,
                    exit_reason=exit_reason,
                    error=error,
                )
            if task.is_terminal:
                exit_reason = (
                    ExitReason.ERROR if task.status == TaskStatus.FAILED else ExitReason.COMPLETED
                )
                error = result.error_message
                return _TurnOutcome(
                    text=text,
                    tools_used=tools_used,
                    artifacts=artifacts,
                    elapsed_s=time.monotonic() - start,
                    exit_reason=exit_reason,
                    error=error,
                )
            self.last_session_id = result.session_id or self.last_session_id
            if result.errored:
                exit_reason = ExitReason.ERROR
                error = result.error_message
                task.transition(TaskStatus.FAILED)
            else:
                task.transition(TaskStatus.COMPLETED)
        except asyncio.CancelledError:
            exit_reason = ExitReason.CANCELLED
            task.cancel("external CLI turn cancelled")
            await self.cancel()
            raise

        return _TurnOutcome(
            text=text,
            tools_used=tools_used,
            artifacts=artifacts,
            elapsed_s=time.monotonic() - start,
            exit_reason=exit_reason,
            error=error,
        )

    def _compose_prompt(self, message: str) -> tuple[str, str]:
        """First-turn vs. resume prompt branching. Resume turns cannot re-inject
        system prompt, so the suffix is prepended to the user message instead.
        Centralising here means the 9 adapters each see a single stable shape."""
        first_turn = self.last_session_id is None
        extra = self._custom_prompt_suffix or ""
        if first_turn:
            return message, extra
        if not extra:
            return message, ""
        return f"{extra}\n\n{message}", ""

    def _build_delegation_result(self, outcome: _TurnOutcome) -> dict:
        """Returned as a plain dict to avoid importing from the larger delegation
        module. Callers cast via `DelegationResult(**d)` where they need the type."""
        return {
            "agent_id": self.profile.id,
            "profile_id": self.profile.id,
            "text": outcome.text,
            "tools_used": outcome.tools_used,
            "artifacts": outcome.artifacts,
            "elapsed_s": outcome.elapsed_s,
            "exit_reason": outcome.exit_reason.value,
        }
