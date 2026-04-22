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
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_providers._common import stream_cli_subprocess
from openakita.agents.cli_runner import (
    CliRunRequest,
    ExitReason,
    ProviderRunResult,
)
from openakita.agents.profile import CliPermissionMode
from openakita.tools.errors import ErrorType, ToolError, classify_cli_error
from openakita.tools.mcp_catalog import MCPCatalog
from openakita.utils.path_helper import which_command

logger = logging.getLogger(__name__)

SESSION_ROOT: Path = Path.home() / ".codex" / "sessions"
CLI_PROVIDER_ID: CliProviderId = CliProviderId.CODEX


def _resolve_binary() -> str | None:
    return which_command("codex")


def write_mcp_config(
    dst_dir: Path,
    mcp_servers: tuple[str, ...],
    *,
    fmt: str,
) -> Path | None:
    """Write an MCP configuration file under `dst_dir` in the requested shape.

    - `fmt="toml"` -> writes `config.toml` with `[mcp_servers.<id>]` sections
      (Codex expects this inside `$CODEX_HOME`).
    - `fmt="json"` -> writes `mcp.json` with a `{"mcpServers": {…}}` object
      (Claude Code's `--mcp-config` contract).

    Returns None when `mcp_servers` is empty — caller should skip the flag.
    The concrete server command + args come from MCPCatalog.get_server(name);
    a missing catalog entry is logged and skipped (the CLI will error naturally
    if it needs that server).
    """
    if not mcp_servers:
        return None

    try:
        catalog = MCPCatalog()
    except Exception as exc:
        logger.warning("codex.write_mcp_config: catalog unavailable: %s", exc)
        catalog = None

    launch_specs: dict[str, dict] = {}
    for server_id in mcp_servers:
        spec = None
        if catalog is not None:
            info = catalog.get_server(server_id)
            if info is not None and info.command:
                spec = {
                    "command": info.command,
                    "args": list(info.args),
                    "env": dict(info.env),
                }
        if spec is None:
            logger.warning("codex.write_mcp_config: no catalog entry for %r", server_id)
            continue
        launch_specs[server_id] = spec

    if not launch_specs:
        return None

    if fmt == "json":
        path = dst_dir / "mcp.json"
        path.write_text(json.dumps({"mcpServers": launch_specs}, indent=2))
        return path

    if fmt == "toml":
        path = dst_dir / "config.toml"
        lines: list[str] = []
        for server_id, spec in launch_specs.items():
            section = server_id.replace("-", "_")
            lines.append(f"[mcp_servers.{section}]")
            cmd = spec.get("command")
            args = spec.get("args") or []
            env = spec.get("env") or {}
            if cmd:
                lines.append(f'command = {json.dumps(cmd)}')
            if args:
                lines.append(f"args = {json.dumps(list(args))}")
            if env:
                lines.append("env = { " + ", ".join(
                    f'{k} = {json.dumps(v)}' for k, v in env.items()
                ) + " }")
            lines.append("")
        path.write_text("\n".join(lines))
        return path

    raise ValueError(f"unknown fmt={fmt!r}")


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
            raise ToolError(
                error_type=ErrorType.DEPENDENCY,
                tool_name="codex",
                message="codex binary not found on PATH",
            )
        argv = [binary, "exec", "--json"]
        if request.profile.cli_permission_mode == CliPermissionMode.WRITE:
            argv.append("--skip-git-repo-check")
        if request.resume_id:
            argv += ["--session", request.resume_id]
        argv.append(request.message)
        return argv

    def build_env(self, request: CliRunRequest) -> dict[str, str]:
        env = dict(os.environ)
        # CODEX_HOME is populated in run() with an absolute per-turn tempdir.
        # For build_env introspection (tests) we allocate a stub path so callers
        # can assert CODEX_HOME is set; run() overwrites before spawn.
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

        def track(proc: asyncio.subprocess.Process) -> None:
            proc_ref["p"] = proc
            on_spawn(proc)

        with tempfile.TemporaryDirectory(prefix="codex-home-") as tmp:
            home_dir = Path(tmp)
            write_mcp_config(home_dir, request.mcp_servers, fmt="toml")
            if request.system_prompt_extra and not request.resume_id:
                _write_agents_override(home_dir, request.system_prompt_extra)
            env = {**env, "CODEX_HOME": str(home_dir)}

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
