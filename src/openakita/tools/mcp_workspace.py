"""
MCP workspace operations — shared between handler and API routes.

Consolidates add/remove/sync logic that was previously duplicated across
tools/handlers/mcp.py and api/routes/mcp.py.  All functions operate on
MCPClient + MCPCatalog via their public APIs.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from .mcp import MCPClient, MCPServerConfig
from .mcp_catalog import MCPCatalog

logger = logging.getLogger(__name__)


def sync_tools_after_connect(
    server_name: str,
    client: MCPClient,
    catalog: MCPCatalog,
) -> int:
    """Sync runtime tools from MCPClient to MCPCatalog after a successful connect.

    Returns the number of tools synced.
    """
    tools = client.list_tools(server_name)
    if not tools:
        return 0
    tool_dicts = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]
    return catalog.sync_tools_from_client(server_name, tool_dicts, force=True)


def _resolve_stdio_args(args: list[str], search_bases: list[Path]) -> list[str]:
    """Resolve relative paths in stdio args to absolute paths."""
    resolved = list(args)
    for i, arg in enumerate(resolved):
        if arg.startswith("-") or Path(arg).is_absolute():
            continue
        for base in search_bases:
            candidate = base / arg
            if candidate.is_file():
                resolved[i] = str(candidate.resolve())
                logger.info("Resolved relative arg '%s' -> '%s'", arg, resolved[i])
                break
    return resolved


async def add_server_to_workspace(
    name: str,
    transport: str,
    command: str,
    args: list[str],
    env: dict[str, str],
    url: str,
    description: str,
    instructions: str,
    auto_connect: bool,
    *,
    config_base_dir: Path,
    search_bases: list[Path],
    client: MCPClient,
    catalog: MCPCatalog,
) -> dict:
    """Create config dir, write SERVER_METADATA.json, register, and optionally connect.

    Returns a result dict with keys: status, server, path, connect_result.
    """
    server_dir = config_base_dir / name
    server_dir.mkdir(parents=True, exist_ok=True)

    resolved_args = (
        _resolve_stdio_args(args, [server_dir, *search_bases])
        if transport == "stdio"
        else list(args)
    )

    metadata = {
        "serverIdentifier": name,
        "serverName": description or name,
        "command": command,
        "args": resolved_args,
        "env": env,
        "transport": transport,
        "url": url,
        "autoConnect": auto_connect,
    }

    metadata_file = server_dir / "SERVER_METADATA.json"
    metadata_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if instructions:
        (server_dir / "INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")

    catalog.scan_mcp_directory(config_base_dir)
    catalog.invalidate_cache()

    client.add_server(MCPServerConfig(
        name=name,
        command=command,
        args=resolved_args,
        env=env,
        description=description,
        transport=transport,
        url=url,
        cwd=str(server_dir),
    ))

    connect_result = None
    result = await client.connect(name)
    if result.success:
        synced = sync_tools_after_connect(name, client, catalog)
        connect_result = {"connected": True, "tool_count": result.tool_count, "synced": synced}
    else:
        connect_result = {"connected": False, "error": result.error}

    return {
        "status": "ok",
        "server": name,
        "path": str(server_dir),
        "connect_result": connect_result,
    }


async def remove_server_from_workspace(
    name: str,
    *,
    config_base_dir: Path,
    builtin_dir: Path | None,
    client: MCPClient,
    catalog: MCPCatalog,
) -> dict:
    """Disconnect, delete config dir, remove from client/catalog.

    Returns a result dict with keys: status, server, removed, message (optional).
    """
    server_dir = config_base_dir / name

    if not server_dir.exists():
        if builtin_dir and (builtin_dir / name).exists():
            return {
                "status": "error",
                "message": f"{name} is a built-in server and cannot be removed",
            }
        return {"status": "error", "message": f"Server not found: {name}"}

    if client.is_connected(name):
        await client.disconnect(name)

    shutil.rmtree(server_dir, ignore_errors=True)

    client.remove_server(name)
    catalog.remove_server(name)

    return {"status": "ok", "server": name, "removed": True}


async def reload_all_servers(
    client: MCPClient,
    catalog: MCPCatalog,
    scan_dirs: list[Path],
) -> dict:
    """Disconnect all, clear state, re-scan config dirs, re-register to client.

    Returns a result dict with counts.
    """
    connected = list(client.list_connected())
    await client.reset()
    catalog.reset()

    total_count = 0
    for dir_path in scan_dirs:
        if dir_path.exists():
            count = catalog.scan_mcp_directory(dir_path)
            if count > 0:
                total_count += count

    for server in catalog.servers:
        if not server.identifier:
            continue
        transport = server.transport or "stdio"
        if transport == "stdio" and not server.command:
            continue
        if transport in ("streamable_http", "sse") and not server.url:
            continue
        client.add_server(MCPServerConfig(
            name=server.identifier,
            command=server.command or "",
            args=list(server.args or []),
            env=dict(server.env or {}),
            description=server.name or "",
            transport=transport,
            url=server.url or "",
            cwd=server.config_dir or "",
        ))

    return {
        "catalog_count": catalog.server_count,
        "client_count": len(client.list_servers()),
        "previously_connected": len(connected),
    }
