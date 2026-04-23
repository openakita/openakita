"""Shared helpers for provider adapters.

stream_cli_subprocess() spawns a subprocess with asyncio.create_subprocess_exec
(shell=False always), calls the caller's on_spawn hook synchronously so the
runner can track the process for signal escalation, then yields stdout lines
until EOF or cancellation.

write_mcp_config() emits an MCP server config for the two CLIs that read one —
Codex (`config.toml`) and Claude Code (`mcp.json`). Lives here so neither
adapter has to reach into the other.

Streaming-only. Does NOT replace the blocking _run_cmd helpers in
tools/handlers/opencli.py — those are one-shot Popen.communicate calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from openakita.tools.errors import ErrorType, ToolError
from openakita.tools.mcp_catalog import MCPCatalog
from openakita.utils.path_helper import get_enriched_env

if TYPE_CHECKING:
    from openakita.agents.profile import AgentProfile

logger = logging.getLogger(__name__)


# Allow-list of environment variables copied from the OpenAkita server process
# into external-CLI subprocesses. Excludes LLM provider secrets, API keys, and
# OpenAkita-specific config so child CLIs use their own credentials, not ours.
_CLI_ENV_ALLOW_EXACT = frozenset(
    {
        # Identity / session essentials
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "PATH",
        "PWD",
        # Temp dirs
        "TMPDIR",
        "TMP",
        "TEMP",
        # Locale / terminal
        "LANG",
        "TERM",
        "TERMINFO",
        "COLORTERM",
        "TERM_PROGRAM",
        "TERM_PROGRAM_VERSION",
        "NO_COLOR",
        "FORCE_COLOR",
        "CLICOLOR",
        "CLICOLOR_FORCE",
        "TZ",
        # SSH / GPG agent forwarding — preserves git-over-ssh and commit signing.
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "GPG_AGENT_INFO",
        "GNUPGHOME",
        # Windows (no-op on Linux/macOS when unset)
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
    }
)
_CLI_ENV_ALLOW_PREFIXES = ("LC_", "XDG_")

_CLI_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")
_FALLBACK_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Default asyncio StreamReader buffer is 64 KiB; a single NDJSON line from
# Claude Code / Codex / Goose easily exceeds that when it embeds a large tool
# result (file read, grep, plan echo). 64 MiB is generous for any realistic
# CLI event while still capping a runaway child process.
_CLI_STDOUT_BUFFER_LIMIT = 64 * 1024 * 1024
_CLI_STDERR_BUFFER_LIMIT = 256 * 1024
_CLI_STDERR_CHUNK_SIZE = 8192


def _copy_safe_base_env() -> dict[str, str]:
    """Copy the allow-listed env vars from os.environ into a fresh dict."""
    base: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _CLI_ENV_ALLOW_EXACT or key.startswith(_CLI_ENV_ALLOW_PREFIXES):
            base[key] = value
    # Ensure PATH is always present so get_enriched_env can merge into it.
    base.setdefault("PATH", _FALLBACK_PATH)
    return base


def _resolve_cli_env_value(value: str, environ: Mapping[str, str] | None = None) -> str:
    """Replace ``${VAR}`` patterns in *value* with environ lookups.

    Missing references resolve to empty string (matches mcp_catalog's
    ``_resolve_env_vars`` precedent). Single-pass — no recursive expansion.
    """
    env = environ if environ is not None else os.environ
    return _CLI_ENV_VAR_RE.sub(lambda m: env.get(m.group(1), ""), value)


def build_cli_env(profile: AgentProfile | None = None) -> dict[str, str]:
    """Build a subprocess env for CLI adapters.

    Starts from a minimal allow-list (HOME, USER, SHELL, PATH, LANG, LC_*,
    TERM, TMPDIR, XDG_*, SSH_AUTH_SOCK, ...). Deliberately excludes OpenAkita's
    LLM provider secrets (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...) so external
    CLIs don't pick them up by accident.

    Merges the login-shell PATH so child CLIs can still find node, python,
    etc. when the server was launched by systemd with a minimum PATH.

    Overlays *profile.cli_env* last, resolving ``${VAR}`` references against
    os.environ. Profile values win over the base env.
    """
    base = _copy_safe_base_env()
    enriched = get_enriched_env(base)
    env = enriched if enriched is not None else base

    if profile is not None and profile.cli_env:
        for key, raw in profile.cli_env.items():
            if not key:
                continue
            env[key] = _resolve_cli_env_value(raw)

    return env


def binary_not_found_error(
    tool_name: str,
    binary: str,
    install_hint: str,
) -> ToolError:
    """Build a DEPENDENCY ToolError with an actionable install hint.

    The message names searched locations so operators can debug systemd/
    Docker PATH issues without reading source.
    """
    server_path = os.environ.get("PATH", "")
    shell_checked = "yes" if server_path else "no"
    return ToolError(
        error_type=ErrorType.DEPENDENCY,
        tool_name=tool_name,
        message=(
            f"{binary} binary not found on PATH. "
            f"Checked server PATH ({server_path!r}) and login-shell PATH "
            f"(checked={shell_checked}). "
            f"Install hint: {install_hint}. "
            f"If installed via nvm/volta, ensure the openakita server can see "
            f'your user PATH — either add Environment="PATH=..." to the '
            f"systemd unit, or rely on the login-shell PATH fallback (requires "
            f"$SHELL to run .bashrc/.zshrc non-interactively)."
        ),
    )


async def stream_cli_subprocess(
    argv: list[str],
    env: dict[str, str],
    cwd: Path,
    cancelled: asyncio.Event,
    *,
    on_spawn: Callable[[asyncio.subprocess.Process], None],
    on_stderr: Callable[[bytes], None] | None = None,
) -> AsyncIterator[bytes]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env={**env} if env else None,
        limit=_CLI_STDOUT_BUFFER_LIMIT,
    )
    on_spawn(proc)
    assert proc.stdout is not None
    stderr_task: asyncio.Task | None = None

    async def _cancel_and_await(task: asyncio.Task | None) -> None:
        if task is None:
            return
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        kept = 0
        while True:
            try:
                chunk = await proc.stderr.read(_CLI_STDERR_CHUNK_SIZE)
            except Exception:
                logger.debug("stream_cli_subprocess: stderr drain failed", exc_info=True)
                return
            if not chunk:
                return
            if on_stderr is None:
                continue
            remaining = _CLI_STDERR_BUFFER_LIMIT - kept
            if remaining <= 0:
                continue
            trimmed = chunk[:remaining]
            kept += len(trimmed)
            try:
                on_stderr(trimmed)
            except Exception:
                logger.debug("stream_cli_subprocess: on_stderr callback failed", exc_info=True)

    async def _read_stdout_line() -> bytes | None:
        read_task = asyncio.create_task(proc.stdout.readline())
        cancel_task = asyncio.create_task(cancelled.wait())
        try:
            done, _pending = await asyncio.wait(
                {read_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done and read_task not in done:
                await _cancel_and_await(read_task)
                return None
            await _cancel_and_await(cancel_task)
            return await read_task
        finally:
            await _cancel_and_await(read_task)
            await _cancel_and_await(cancel_task)

    async def _await_stderr_drain() -> None:
        if stderr_task is None or stderr_task.done():
            return
        cancel_task = asyncio.create_task(cancelled.wait())
        process_exit_task = asyncio.create_task(proc.wait())
        try:
            done, _pending = await asyncio.wait(
                {stderr_task, cancel_task, process_exit_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stderr_task in done:
                await stderr_task
                return
            if cancel_task in done:
                await _cancel_and_await(stderr_task)
                return
            await process_exit_task
        finally:
            await _cancel_and_await(cancel_task)
            await _cancel_and_await(process_exit_task)

    stderr_task = asyncio.create_task(_drain_stderr())
    try:
        while True:
            if cancelled.is_set():
                return
            try:
                line = await _read_stdout_line()
            except ValueError as exc:
                logger.error(
                    "stream_cli_subprocess: stdout line exceeded %d-byte buffer; "
                    "terminating stream. Detail: %s",
                    _CLI_STDOUT_BUFFER_LIMIT,
                    exc,
                )
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                return
            if line is None:
                return
            if not line:
                await _await_stderr_drain()
                return
            yield line
    finally:
        await _cancel_and_await(stderr_task)


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
        logger.warning("write_mcp_config: catalog unavailable: %s", exc)
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
            logger.warning("write_mcp_config: no catalog entry for %r", server_id)
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
                lines.append(f"command = {json.dumps(cmd)}")
            if args:
                lines.append(f"args = {json.dumps(list(args))}")
            if env:
                lines.append(
                    "env = { " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items()) + " }"
                )
            lines.append("")
        path.write_text("\n".join(lines))
        return path

    raise ValueError(f"unknown fmt={fmt!r}")
