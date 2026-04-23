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
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from openakita.tools.errors import ErrorType, ToolError
from openakita.tools.mcp_catalog import MCPCatalog
from openakita.utils.path_helper import get_enriched_env

logger = logging.getLogger(__name__)


def build_cli_env() -> dict[str, str]:
    """Build a subprocess env for CLI adapters.

    Starts from os.environ, then merges the user's login-shell PATH so
    child CLIs (claude, codex, goose, …) can find their own dependencies
    (node, python, etc.) even when the openakita server was started by
    systemd with a minimum PATH.
    """
    enriched = get_enriched_env(dict(os.environ))
    # get_enriched_env returns its input unchanged when no enrichment is
    # available, never None in this code path — but keep a defensive
    # fallback to satisfy the type checker.
    return enriched if enriched is not None else dict(os.environ)


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
            f"your user PATH — either add Environment=\"PATH=...\" to the "
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
) -> AsyncIterator[bytes]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env={**env} if env else None,
    )
    on_spawn(proc)
    assert proc.stdout is not None
    while True:
        if cancelled.is_set():
            return
        read_task = asyncio.create_task(proc.stdout.readline())
        cancel_task = asyncio.create_task(cancelled.wait())
        done, _pending = await asyncio.wait(
            {read_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task in done and read_task not in done:
            read_task.cancel()
            return
        cancel_task.cancel()
        line = await read_task
        if not line:
            return
        yield line


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
