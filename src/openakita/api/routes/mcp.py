"""
MCP (Model Context Protocol) management routes.

Provides HTTP API for the frontend to manage MCP servers:
- List configured servers and their status
- Connect/disconnect servers
- View available tools per server
- Add/remove server configs
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from openakita.tools.mcp_workspace import (
    add_server_to_workspace,
    remove_server_from_workspace,
    sync_tools_after_connect,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_agent(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return None
    if hasattr(agent, "mcp_client"):
        return agent
    local = getattr(agent, "_local_agent", None)
    if local and hasattr(local, "mcp_client"):
        return local
    return None


def _get_mcp_client(request: Request):
    agent = _get_agent(request)
    return agent.mcp_client if agent else None


def _get_mcp_catalog(request: Request):
    agent = _get_agent(request)
    return agent.mcp_catalog if agent else None


def _sync_tools_to_catalog(request: Request, server_name: str, client):
    """连接成功后将运行时工具同步到 catalog（MCPCatalog 内部缓存自动失效）"""
    catalog = _get_mcp_catalog(request)
    if catalog:
        sync_tools_after_connect(server_name, client, catalog)


class MCPServerAddRequest(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""
    description: str = ""
    auto_connect: bool = False


class MCPConnectRequest(BaseModel):
    server_name: str


@router.get("/api/mcp/servers")
async def list_mcp_servers(request: Request):
    """List all MCP servers with their config and connection status."""
    client = _get_mcp_client(request)
    catalog = _get_mcp_catalog(request)

    if client is None:
        return {"error": "Agent not initialized", "servers": []}

    from openakita.config import settings
    if not settings.mcp_enabled:
        return {"mcp_enabled": False, "servers": [], "message": "MCP is disabled"}

    configured = client.list_servers()
    connected = client.list_connected()

    servers = []
    for name in configured:
        server_config = client.get_server_config(name)
        tools = client.list_tools(name)

        catalog_info = None
        if catalog:
            for s in catalog.servers:
                if s.identifier == name:
                    catalog_info = s
                    break

        workspace_dir = settings.mcp_config_path / name
        source = "workspace" if workspace_dir.exists() else "builtin"

        servers.append({
            "name": name,
            "description": server_config.description if server_config else "",
            "transport": server_config.transport if server_config else "stdio",
            "url": server_config.url if server_config else "",
            "command": server_config.command if server_config else "",
            "connected": name in connected,
            "tools": [
                {"name": t.name, "description": t.description}
                for t in tools
            ],
            "tool_count": len(tools),
            "has_instructions": bool(
                catalog_info and catalog_info.instructions
            ) if catalog_info else False,
            "catalog_tool_count": len(catalog_info.tools) if catalog_info else 0,
            "source": source,
            "removable": source == "workspace",
        })

    return {
        "mcp_enabled": True,
        "servers": servers,
        "total": len(servers),
        "connected": len(connected),
        "workspace_path": str(settings.mcp_config_path),
    }


@router.post("/api/mcp/connect")
async def connect_mcp_server(request: Request, body: MCPConnectRequest):
    """Connect to a specific MCP server."""
    client = _get_mcp_client(request)
    if client is None:
        return {"error": "Agent not initialized"}

    if body.server_name in client.list_connected():
        tools = client.list_tools(body.server_name)
        return {
            "status": "already_connected",
            "server": body.server_name,
            "tools": [{"name": t.name, "description": t.description} for t in tools],
        }

    result = await client.connect(body.server_name)
    if result.success:
        _sync_tools_to_catalog(request, body.server_name, client)
        tools = client.list_tools(body.server_name)
        return {
            "status": "connected",
            "server": body.server_name,
            "tools": [{"name": t.name, "description": t.description} for t in tools],
            "tool_count": result.tool_count,
        }
    else:
        return {
            "status": "failed",
            "server": body.server_name,
            "error": result.error or "连接失败（未知原因）",
        }


@router.post("/api/mcp/disconnect")
async def disconnect_mcp_server(request: Request, body: MCPConnectRequest):
    """Disconnect from a specific MCP server."""
    client = _get_mcp_client(request)
    if client is None:
        return {"error": "Agent not initialized"}

    if body.server_name not in client.list_connected():
        return {"status": "not_connected", "server": body.server_name}

    await client.disconnect(body.server_name)
    return {"status": "disconnected", "server": body.server_name}


@router.get("/api/mcp/tools")
async def list_mcp_tools(request: Request, server: str | None = None):
    """List all available MCP tools, optionally filtered by server."""
    client = _get_mcp_client(request)
    if client is None:
        return {"error": "Agent not initialized", "tools": []}

    tools = client.list_tools(server)
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ],
        "total": len(tools),
    }


@router.get("/api/mcp/instructions/{server_name}")
async def get_mcp_instructions(request: Request, server_name: str):
    """Get INSTRUCTIONS.md for a specific MCP server."""
    catalog = _get_mcp_catalog(request)
    if catalog is None:
        return {"error": "Agent not initialized"}

    instructions = catalog.get_server_instructions(server_name)
    if instructions:
        return {"server": server_name, "instructions": instructions}
    return {"server": server_name, "instructions": None, "message": "No instructions available"}


@router.post("/api/mcp/servers/add")
async def add_mcp_server(request: Request, body: MCPServerAddRequest):
    """Add a new MCP server config (persisted to workspace data/mcp/servers/)."""
    import re
    from pathlib import Path

    from openakita.tools.mcp import VALID_TRANSPORTS

    if not body.name.strip():
        return {"status": "error", "message": "服务器名称不能为空"}
    if not re.match(r'^[a-zA-Z0-9_-]+$', body.name.strip()):
        return {"status": "error", "message": "服务器名称只能包含字母、数字、连字符和下划线"}
    if body.transport not in VALID_TRANSPORTS:
        return {"status": "error", "message": f"不支持的传输协议: {body.transport}（支持: {', '.join(sorted(VALID_TRANSPORTS))}）"}
    if body.transport == "stdio" and not body.command.strip():
        return {"status": "error", "message": "stdio 模式需要填写启动命令"}
    if body.transport in ("streamable_http", "sse") and not body.url.strip():
        return {"status": "error", "message": f"{body.transport} 模式需要填写 URL"}

    client = _get_mcp_client(request)
    catalog = _get_mcp_catalog(request)
    if not client or not catalog:
        return {"status": "error", "message": "Agent not initialized"}

    from openakita.config import settings

    result = await add_server_to_workspace(
        name=body.name.strip(),
        transport=body.transport,
        command=body.command,
        args=body.args,
        env=body.env,
        url=body.url,
        description=body.description,
        instructions="",
        auto_connect=body.auto_connect,
        config_base_dir=settings.mcp_config_path,
        search_bases=[settings.project_root, Path.cwd()],
        client=client,
        catalog=catalog,
    )

    return result


@router.delete("/api/mcp/servers/{server_name}")
async def remove_mcp_server(request: Request, server_name: str):
    """Remove an MCP server config (only workspace configs, not built-in)."""
    client = _get_mcp_client(request)
    catalog = _get_mcp_catalog(request)
    if not client or not catalog:
        return {"status": "error", "message": "Agent not initialized"}

    from openakita.config import settings

    result = await remove_server_from_workspace(
        server_name,
        config_base_dir=settings.mcp_config_path,
        builtin_dir=settings.mcp_builtin_path,
        client=client,
        catalog=catalog,
    )

    return result
