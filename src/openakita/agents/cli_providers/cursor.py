# src/openakita/agents/cli_providers/cursor.py
"""Cursor CLI provider adapter (cursor-agent).

Non-interactive mode:
    cursor-agent --output-format stream-json [--force] [--resume <id>] "<message>"
emits JSONL events. `--force` bypasses the edit-confirmation gate in write mode.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
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
from openakita.agents.profile import CliPermissionMode
from openakita.tools.errors import classify_cli_error
from openakita.utils.path_helper import which_command

logger = logging.getLogger(__name__)

SESSION_ROOT: Path = Path.home() / ".cursor" / "sessions"
CLI_PROVIDER_ID: CliProviderId = CliProviderId.CURSOR


def _resolve_binary() -> str | None:
    return which_command("cursor-agent") or which_command("cursor")


@dataclass(frozen=True)
class _StreamEvent:
    kind: str
    session_id: str | None = None
    text: str = ""
    tool_name: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error_message: str | None = None


def _parse_stream_line(line: bytes) -> _StreamEvent | None:
    if not line or not line.strip():
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None

    etype = obj.get("type")
    if etype == "session_start":
        return _StreamEvent(kind="init", session_id=obj.get("sessionId") or obj.get("session_id"))
    if etype == "assistant":
        return _StreamEvent(kind="assistant_text", text=str(obj.get("text", "")))
    if etype == "tool_use":
        return _StreamEvent(kind="tool_use", tool_name=str(obj.get("name", "")))
    if etype == "done":
        usage = obj.get("usage") or {}
        return _StreamEvent(
            kind="result",
            input_tokens=int(usage.get("input", 0) or 0),
            output_tokens=int(usage.get("output", 0) or 0),
        )
    if etype == "error":
        return _StreamEvent(kind="error", error_message=str(obj.get("message", "error")))
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
            self.input_tokens, self.output_tokens = ev.input_tokens, ev.output_tokens
        elif ev.kind == "error":
            self.errored, self.error_message = True, ev.error_message


class CursorAdapter:
    def build_argv(self, request: CliRunRequest) -> list[str]:
        binary = _resolve_binary()
        if binary is None:
            raise binary_not_found_error(
                tool_name="cursor",
                binary="cursor-agent",
                install_hint="curl https://cursor.com/install -fsS | bash",
            )
        argv = [binary, "--output-format", "stream-json"]
        if request.profile.cli_permission_mode == CliPermissionMode.WRITE:
            argv.append("--force")
        if request.resume_id:
            argv += ["--resume", request.resume_id]
        argv.append(request.message)
        return argv

    def build_env(self, request: CliRunRequest) -> dict[str, str]:
        return build_cli_env()

    async def run(self, request, argv, env, *, on_spawn):
        acc = _TurnAccumulator()
        proc_ref: dict[str, asyncio.subprocess.Process] = {}

        def track(proc):
            proc_ref["p"] = proc
            on_spawn(proc)

        try:
            async for line in stream_cli_subprocess(
                argv, env, request.cwd, request.cancelled, on_spawn=track,
            ):
                ev = _parse_stream_line(line)
                if ev is not None:
                    acc.apply(ev)
        except asyncio.CancelledError:
            return _cancelled_result(acc)

        if request.cancelled.is_set():
            return _cancelled_result(acc)

        proc = proc_ref.get("p")
        exit_code = 0
        stderr_text = ""
        if proc is not None:
            try:
                exit_code = await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                exit_code = -1
            if proc.stderr is not None:
                try:
                    stderr_text = (await proc.stderr.read()).decode("utf-8", "replace")
                except Exception:
                    logger.debug("stderr read failed", exc_info=True)

        if acc.errored or exit_code != 0:
            err_type = classify_cli_error(exit_code=exit_code, stderr=stderr_text, exception=None)
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


PROVIDER: CursorAdapter = CursorAdapter()
