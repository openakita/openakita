"""
MCP handler

Handles MCP-related system skills:
- call_mcp_tool: Call MCP tool
- list_mcp_servers: List servers
- get_mcp_instructions: Get usage instructions
- add_mcp_server: Add server configuration (persisted to workspace)
- remove_mcp_server: Remove server configuration
- connect_mcp_server: Connect to server
- disconnect_mcp_server: Disconnect from server
- reload_mcp_servers: Reload all configurations
"""

import logging
from typing import TYPE_CHECKING, Any

from ..mcp_workspace import (
    add_server_to_workspace,
    reload_all_servers,
    remove_server_from_workspace,
    sync_tools_after_connect,
)

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class MCPHandler:
    """MCP handler"""

    TOOLS = [
        "call_mcp_tool",
        "list_mcp_servers",
        "get_mcp_instructions",
        "add_mcp_server",
        "remove_mcp_server",
        "connect_mcp_server",
        "disconnect_mcp_server",
        "reload_mcp_servers",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle tool invocation"""
        from ...config import settings

        # Management tools are always available (regardless of MCP enablement)
        management_tools = {
            "add_mcp_server": self._add_server,
            "remove_mcp_server": self._remove_server,
            "reload_mcp_servers": self._reload_servers,
        }
        if tool_name in management_tools:
            return await management_tools[tool_name](params)

        if not settings.mcp_enabled:
            return "❌ MCP is disabled. Set MCP_ENABLED=true in .env to enable"

        dispatch = {
            "call_mcp_tool": self._call_tool,
            "list_mcp_servers": self._list_servers,
            "get_mcp_instructions": self._get_instructions,
            "connect_mcp_server": self._connect_server,
            "disconnect_mcp_server": self._disconnect_server,
        }
        handler_fn = dispatch.get(tool_name)
        if handler_fn:
            return await handler_fn(params)
        return f"❌ Unknown MCP tool: {tool_name}"

    # ==================== Tool invocation ====================

    async def _call_tool(self, params: dict) -> str:
        """Call MCP tool"""
        server = params["server"]
        mcp_tool_name = params["tool_name"]
        arguments = params.get("arguments", {})

        catalog = self.agent.mcp_catalog
        server_info = catalog.get_server(server) if catalog else None
        if server_info and not server_info.enabled:
            return f"❌ MCP server {server} is disabled and cannot be called"
        if catalog and hasattr(catalog, "has_server") and not catalog.has_server(server):
            return f"❌ MCP server '{server}' is not available for this Agent"

        client = self.agent.mcp_client

        auto_connected = False
        if not client.is_connected(server):
            from ..mcp_workspace import prepare_chrome_devtools_args

            await prepare_chrome_devtools_args(client, server)
            result = await client.connect(server)
            if not result.success:
                return f"❌ Failed to connect to MCP server {server}: {result.error}"
            auto_connected = True

        result = await client.call_tool(server, mcp_tool_name, arguments)

        if auto_connected or result.reconnected:
            self._sync_catalog(server)

        if result.success:
            from ...utils.credential_redact import redact_credentials

            safe_data = redact_credentials(str(result.data)) if result.data else ""
            return f"✅ MCP tool call succeeded:\n{safe_data}"
        else:
            return f"❌ MCP tool call failed: {result.error}"

    async def _list_servers(self, params: dict) -> str:
        """List MCP servers and their tools"""
        catalog_servers = self.agent.mcp_catalog.list_servers()
        connected = self.agent.mcp_client.list_connected()

        all_ids = sorted(catalog_servers)

        if not all_ids:
            return (
                "No MCP servers configured yet\n\n"
                "Tip: Use the add_mcp_server tool to add servers, or configure manually in the mcps/ directory"
            )

        from ...config import settings

        output = f"Configured {len(all_ids)} MCP servers:\n\n"

        for server_id in all_ids:
            is_connected = server_id in connected
            status = "🟢 Connected" if is_connected else "⚪ Not connected"

            workspace_dir = settings.mcp_config_path / server_id
            source = "📁 Workspace" if workspace_dir.exists() else "📦 Built-in"
            output += f"### {server_id} {status} [{source}]\n"

            tools = self.agent.mcp_client.list_tools(server_id)
            if tools:
                for t in tools:
                    output += f"- **{t.name}**: {t.description}\n"
            elif is_connected:
                output += "- *(No tools)*\n"
            else:
                catalog_tools = self.agent.mcp_catalog.list_tools(server_id)
                if catalog_tools:
                    for t in catalog_tools:
                        output += f"- **{t.name}**: {t.description}\n"
                else:
                    output += "- *(Not connected; connect with `connect_mcp_server` to discover tools)*\n"
            output += "\n"

        output += (
            "**Available actions**:\n"
            "- `call_mcp_tool(server, tool_name, arguments)` Call tool\n"
            "- `connect_mcp_server(server)` Connect to server\n"
            "- `get_mcp_instructions(server)` Get detailed usage instructions\n"
            "- `add_mcp_server(name, ...)` Add new server\n"
            "- `remove_mcp_server(name)` Remove server"
        )
        return output

    async def _get_instructions(self, params: dict) -> str:
        """Get MCP usage instructions"""
        server = params["server"]
        instructions = self.agent.mcp_catalog.get_server_instructions(server)

        if instructions:
            return f"# MCP server {server} Usage Instructions\n\n{instructions}"
        else:
            return f"❌ No usage instructions found for server {server}, or server does not exist"

    def _sync_catalog(self, server: str) -> None:
        """Sync runtime tools to catalog (MCPCatalog internal cache auto-invalidates)"""
        sync_tools_after_connect(server, self.agent.mcp_client, self.agent.mcp_catalog)
        logger.info("MCP catalog synced for %s", server)

    # ==================== Connection management ====================

    async def _connect_server(self, params: dict) -> str:
        """Connect to MCP server"""
        server = params["server"]
        catalog = self.agent.mcp_catalog
        client = self.agent.mcp_client

        if catalog and hasattr(catalog, "has_server") and not catalog.has_server(server):
            return f"❌ MCP server '{server}' is not available for this Agent"

        if client.is_connected(server):
            tools = client.list_tools(server)
            return f"✅ Connected to {server} ({len(tools)} tools available)"

        if not client.has_server(server):
            return f"❌ Server {server} not configured. Use add_mcp_server to add or check the name"

        from ..mcp_workspace import prepare_chrome_devtools_args

        await prepare_chrome_devtools_args(client, server)
        result = await client.connect(server)
        if result.success:
            self._sync_catalog(server)
            tools = client.list_tools(server)
            tool_names = [t.name for t in tools]
            return (
                f"✅ Connected to MCP server: {server}\n"
                f"Discovered {len(tools)} tools: {', '.join(tool_names)}"
            )
        else:
            return f"❌ Failed to connect to MCP server: {server}\nReason: {result.error}"

    async def _disconnect_server(self, params: dict) -> str:
        """Disconnect from MCP server"""
        server = params["server"]
        client = self.agent.mcp_client

        if not client.is_connected(server):
            return f"⚪ Server {server} is not connected"

        await client.disconnect(server)
        return f"✅ Disconnected from MCP server: {server}"

    # ==================== Configuration management ====================

    async def _add_server(self, params: dict) -> str:
        """Add MCP server configuration to workspace"""
        from pathlib import Path

        from ...config import settings
        from ..mcp import VALID_TRANSPORTS

        name = params.get("name", "").strip()
        if not name:
            return "❌ Server name cannot be empty"

        transport = params.get("transport", "stdio")
        if transport not in VALID_TRANSPORTS:
            return (
                f"❌ Unsupported transport protocol: {transport} (supported: {', '.join(sorted(VALID_TRANSPORTS))})"
            )

        command = params.get("command", "")
        url = params.get("url", "")

        if transport == "stdio" and not command:
            return "❌ stdio mode requires the command parameter"
        if transport in ("streamable_http", "sse") and not url:
            return f"❌ {transport} mode requires the url parameter"

        result = await add_server_to_workspace(
            name=name,
            transport=transport,
            command=command,
            args=params.get("args", []),
            env=params.get("env", {}),
            url=url,
            description=params.get("description", name),
            instructions=params.get("instructions", ""),
            auto_connect=params.get("auto_connect", False),
            headers=params.get("headers") or None,
            config_base_dir=settings.mcp_config_path,
            search_bases=[settings.project_root, Path.cwd()],
            client=self.agent.mcp_client,
            catalog=self.agent.mcp_catalog,
        )

        cr = result.get("connect_result") or {}
        if cr.get("connected"):
            tools = self.agent.mcp_client.list_tools(name)
            tool_names = [t.name for t in tools]
            connect_msg = f"\n\n✅ Auto-connected, discovered {len(tools)} tools: {', '.join(tool_names)}"
        else:
            connect_msg = (
                f"\n\n⚠️ Auto-connection failed: {cr.get('error', 'unknown')}\n"
                f'Configuration saved; you can retry later by calling `connect_mcp_server("{name}")`'
            )

        return (
            f"✅ Added MCP server: {name}\n"
            f"  Transport: {transport}\n"
            f"  Config path: {result['path']}"
            f"{connect_msg}"
        )

    async def _remove_server(self, params: dict) -> str:
        """Remove MCP server configuration"""
        from ...config import settings

        name = params.get("name", "").strip()
        if not name:
            return "❌ Server name cannot be empty"

        result = await remove_server_from_workspace(
            name,
            config_base_dir=settings.mcp_config_path,
            builtin_dir=settings.mcp_builtin_path,
            client=self.agent.mcp_client,
            catalog=self.agent.mcp_catalog,
        )

        if result["status"] == "error":
            return f"❌ {result['message']}"
        return f"✅ Removed MCP server: {name}"

    async def _reload_servers(self, params: dict) -> str:
        """Reload all MCP configurations

        Directly manipulates the globally shared mcp_client/mcp_catalog to avoid
        calling _load_mcp_servers() on pool agents (which would trigger
        _start_builtin_mcp_servers and other initialization logic that should
        only run on master agents).
        """
        from ...config import settings

        scan_dirs = [
            settings.mcp_builtin_path,
            settings.project_root / ".mcp",
            settings.mcp_config_path,
        ]

        counts = await reload_all_servers(
            client=self.agent.mcp_client,
            catalog=self.agent.mcp_catalog,
            scan_dirs=scan_dirs,
        )

        return (
            f"✅ MCP configuration reloaded\n"
            f"  In catalog: {counts['catalog_count']} servers\n"
            f"  Connectable: {counts['client_count']} servers\n"
            f"  Previously connected {counts['previously_connected']} servers disconnected\n\n"
            f"Use `connect_mcp_server(server)` to reconnect"
        )


def create_handler(agent: "Agent"):
    """Create MCP handler"""
    handler = MCPHandler(agent)
    return handler.handle
