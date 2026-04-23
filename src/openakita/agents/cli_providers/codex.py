# src/openakita/agents/cli_providers/codex.py
"""Codex CLI provider adapter.

Per-run isolation: each `run()` allocates a `tempfile.TemporaryDirectory` as
`CODEX_HOME`, writes `config.toml` (MCP servers) and optionally
`AGENTS.override.md` (first-turn system-prompt channel), then cleans up in
`finally`. The directory lives only for the duration of one turn — adapters
stay reentrant.

Session history lives at `~/.codex/sessions/<session-id>.jsonl`. The
`SESSION_ROOT` constant is consumed by `api/routes/sessions.py` for the
external-CLI listing endpoints.
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_providers._common import (
    binary_not_found_error,
    build_cli_env,
    stream_cli_subprocess,
    write_mcp_config,
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

SESSION_ROOT: Path = Path.home() / ".codex" / "sessions"
CLI_PROVIDER_ID: CliProviderId = CliProviderId.CODEX


def _resolve_binary() -> str | None:
    return which_command("codex")


def _write_agents_override(dst_dir: Path, content: str) -> Path | None:
    if not content:
        return None
    path = dst_dir / "AGENTS.override.md"
    path.write_text(content)
    return path


@dataclass(frozen=True)
class _StreamEvent:
    kind: str                         # "init" | "assistant_text" | "tool_use" | "result" | "error"
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

    etype = obj.get("type") or obj.get("event")
    if etype in ("session_start", "init"):
        return _StreamEvent(kind="init", session_id=obj.get("session_id") or obj.get("id"))
    if etype in ("assistant_delta", "message_delta"):
        return _StreamEvent(
            kind="assistant_text",
            text=str(obj.get("text", "") or obj.get("delta", "")),
        )
    if etype in ("tool_call", "tool_use"):
        return _StreamEvent(kind="tool_use", tool_name=str(obj.get("name", "")))
    if etype in ("turn_end", "result"):
        if obj.get("error") or obj.get("is_error"):
            return _StreamEvent(
                kind="error",
                error_message=str(obj.get("error") or obj.get("result") or "unknown"),
            )
        usage = obj.get("usage") or {}
        return _StreamEvent(
            kind="result",
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )
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


class CodexAdapter:
    """Codex CLI adapter. Stateless across calls — per-turn state lives in run()."""

    def build_argv(self, request: CliRunRequest) -> list[str]:
        binary = _resolve_binary()
        if binary is None:
            raise binary_not_found_error(
                tool_name="codex",
                binary="codex",
                install_hint="npm install -g @openai/codex",
            )
        argv = [binary, "exec", "--json"]
        if request.profile.cli_permission_mode == CliPermissionMode.WRITE:
            argv.append("--skip-git-repo-check")
        if request.resume_id:
            argv += ["--session", request.resume_id]
        argv.append(request.message)
        return argv

    def build_env(self, request: CliRunRequest) -> dict[str, str]:
        env = build_cli_env(request.profile)
        # CODEX_HOME is populated in run() with an absolute per-turn tempdir.
        # For build_env introspection (tests) we allocate a stub path so callers
        # can assert CODEX_HOME is set; run() overwrites before spawn.
        # Note: if a user sets CODEX_HOME in profile.cli_env it surfaces here
        # via build_cli_env's overlay, but run() still overrides it with a
        # per-turn tempdir to keep MCP config isolated between runs.
        env["CODEX_HOME"] = env.get("CODEX_HOME") or str(
            Path(tempfile.gettempdir()) / "codex-home-stub"
        )
        return env

    async def run(
        self,
        request: CliRunRequest,
        argv: list[str],
        env: dict[str, str],
        *,
        on_spawn: Callable[[asyncio.subprocess.Process], None],
    ) -> ProviderRunResult:
        acc = _TurnAccumulator()
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

        stderr_buffer: list[bytes] = []

        with tempfile.TemporaryDirectory(prefix="codex-home-") as tmp:
            home_dir = Path(tmp)
            write_mcp_config(home_dir, request.mcp_servers, fmt="toml")
            if request.system_prompt_extra and not request.resume_id:
                _write_agents_override(home_dir, request.system_prompt_extra)
            env = {**env, "CODEX_HOME": str(home_dir)}

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


PROVIDER: CodexAdapter = CodexAdapter()
