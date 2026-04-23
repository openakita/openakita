# src/openakita/agents/cli_providers/claude_code.py
"""Claude Code provider adapter.

Wraps the `claude` CLI in `--print --verbose --output-format stream-json` mode,
parses JSONL events into a `ProviderRunResult`, and snapshots `git diff
--name-only` around write-mode turns so the orchestrator sees edited paths
as artifacts.

Stateless across calls: every per-turn value lives in locals inside `run()`.
`ExternalCliAgent` owns `_last_session_id` and feeds it back via
`CliRunRequest.resume_id`.

Session-history: historical transcripts live under
    `~/.claude/projects/<cwd-hash>/*.jsonl`
The `SESSION_ROOT` constant below is the anchor consumed by
`api/routes/sessions.py` for the external-CLI listing endpoints.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
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

SESSION_ROOT: Path = Path.home() / ".claude" / "projects"
CLI_PROVIDER_ID: CliProviderId = CliProviderId.CLAUDE_CODE


def _resolve_binary() -> str | None:
    """Return absolute path to `claude` or None if missing. Wrapped so tests can patch."""
    return which_command("claude")


@dataclass(frozen=True)
class _StreamEvent:
    kind: str                          # "init" | "assistant_text" | "tool_use" | "result" | "error"
    session_id: str | None = None
    text: str = ""
    tool_name: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error_message: str | None = None


def _parse_stream_line(line: bytes) -> _StreamEvent | None:
    """Parse one JSONL line from `claude --output-format stream-json`.

    Returns None for blank lines and non-JSON garbage -- the caller logs-and-drops.
    Unknown event shapes also return None so the parser is future-proof to new
    Claude Code stream additions.
    """
    if not line or not line.strip():
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None

    etype = obj.get("type")
    if etype == "system" and obj.get("subtype") == "init":
        return _StreamEvent(kind="init", session_id=obj.get("session_id"))

    if etype == "assistant":
        content = (obj.get("message") or {}).get("content") or []
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(str(block.get("text", "")))
            elif btype == "tool_use":
                return _StreamEvent(kind="tool_use", tool_name=str(block.get("name", "")))
        if texts:
            return _StreamEvent(kind="assistant_text", text="".join(texts))
        return None

    if etype == "result":
        is_error = bool(obj.get("is_error", False))
        if is_error:
            return _StreamEvent(
                kind="error",
                error_message=str(obj.get("result") or obj.get("error") or "unknown error"),
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


def _git_diff_names(cwd: Path) -> set[str]:
    """Return a set of file paths currently shown by `git diff --name-only`.

    Returns an empty set when cwd is not a git repo or `git` is missing -- the
    caller compares pre/post snapshots; an empty snapshot just means the diff
    delta is ignored, not that the turn failed.
    """
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return set()
    if completed.returncode != 0:
        return set()
    return {ln.strip() for ln in completed.stdout.splitlines() if ln.strip()}


class ClaudeCodeAdapter:
    """Claude Code CLI adapter. Shares an instance across calls; per-turn MCP tempdirs are cleaned up in run()."""

    def __init__(self) -> None:
        self._mcp_tmpdirs: list[Path] = []

    def build_argv(self, request: CliRunRequest) -> list[str]:
        binary = _resolve_binary()
        if binary is None:
            raise binary_not_found_error(
                tool_name="claude_code",
                binary="claude",
                install_hint="npm install -g @anthropic-ai/claude-code",
            )
        argv = [
            binary,
            "--print",
            "--verbose",
            "--output-format", "stream-json",
        ]
        if request.profile.cli_permission_mode == CliPermissionMode.WRITE:
            argv.append("--dangerously-skip-permissions")
        # MCP config: write a JSON file in a per-run tempdir and reference it.
        # The tempdir lives until cleanup() or the run() finally block drains it.
        if request.mcp_servers:
            tmp = Path(tempfile.mkdtemp(prefix="claude-mcp-"))
            self._mcp_tmpdirs.append(tmp)
            path = write_mcp_config(tmp, request.mcp_servers, fmt="json")
            if path is not None:
                argv += ["--mcp-config", str(path)]
        if request.resume_id:
            argv += ["--resume", request.resume_id]
        if request.system_prompt_extra:
            argv += ["--system-prompt", request.system_prompt_extra]
        # Message is the trailing positional -- Claude Code reads it as the user turn.
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
        pre_diff = _git_diff_names(request.cwd) \
            if request.profile.cli_permission_mode == CliPermissionMode.WRITE else set()

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

        try:
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
                    exit_code=exit_code,
                    stderr=stderr_text,
                    exception=None,
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

            post_diff = _git_diff_names(request.cwd) \
                if request.profile.cli_permission_mode == CliPermissionMode.WRITE else set()
            artifacts = sorted(post_diff - pre_diff)

            return ProviderRunResult(
                final_text="".join(acc.text_parts),
                tools_used=list(acc.tools_used),
                artifacts=artifacts,
                session_id=acc.session_id,
                input_tokens=acc.input_tokens,
                output_tokens=acc.output_tokens,
                exit_reason=ExitReason.COMPLETED,
                errored=False,
                error_message=None,
            )
        finally:
            # Clean up this turn's MCP config tempdir(s) before returning.
            while self._mcp_tmpdirs:
                d = self._mcp_tmpdirs.pop()
                shutil.rmtree(d, ignore_errors=True)

    async def cleanup(self) -> None:
        """Remove any per-run MCP-config tempdirs that outlived their turn."""
        while self._mcp_tmpdirs:
            d = self._mcp_tmpdirs.pop()
            shutil.rmtree(d, ignore_errors=True)


PROVIDER: ClaudeCodeAdapter = ClaudeCodeAdapter()
