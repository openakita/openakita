"""
MCP 处理器

处理 MCP 相关的系统技能：
- call_mcp_tool: 调用 MCP 工具
- list_mcp_servers: 列出服务器
- get_mcp_instructions: 获取使用说明
- add_mcp_server: 添加服务器配置（持久化到工作区）
- remove_mcp_server: 移除服务器配置
- connect_mcp_server: 连接服务器
- disconnect_mcp_server: 断开服务器
- reload_mcp_servers: 重新加载所有配置
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
    """MCP 处理器"""

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
        """处理工具调用"""
        from ...config import settings

        # 管理类工具始终可用（无论 MCP 是否启用）
        management_tools = {
            "add_mcp_server": self._add_server,
            "remove_mcp_server": self._remove_server,
            "reload_mcp_servers": self._reload_servers,
        }
        if tool_name in management_tools:
            return await management_tools[tool_name](params)

        if not settings.mcp_enabled:
            return "❌ MCP 已禁用。请在 .env 中设置 MCP_ENABLED=true 启用"

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

    # ==================== 调用类工具 ====================

    async def _call_tool(self, params: dict) -> str:
        """调用 MCP 工具"""
        server = params["server"]
        mcp_tool_name = params["tool_name"]
        arguments = params.get("arguments", {})
        client = self.agent.mcp_client

        if not client.is_connected(server):
            result = await client.connect(server)
            if not result.success:
                return f"❌ 无法连接到 MCP 服务器 {server}: {result.error}"

        result = await client.call_tool(server, mcp_tool_name, arguments)

        if result.reconnected:
            self._sync_and_refresh(server)

        if result.success:
            return f"✅ MCP 工具调用成功:\n{result.data}"
        else:
            return f"❌ MCP 工具调用失败: {result.error}"

    async def _list_servers(self, params: dict) -> str:
        """列出 MCP 服务器"""
        catalog_servers = self.agent.mcp_catalog.list_servers()
        client_servers = self.agent.mcp_client.list_servers()
        connected = self.agent.mcp_client.list_connected()

        all_ids = sorted(set(catalog_servers) | set(client_servers))

        if not all_ids:
            return (
                "当前没有配置 MCP 服务器\n\n"
                "提示: 使用 add_mcp_server 工具添加服务器，或在 mcps/ 目录下手动配置"
            )

        from ...config import settings
        output = f"已配置 {len(all_ids)} 个 MCP 服务器:\n\n"

        for server_id in all_ids:
            status = "🟢 已连接" if server_id in connected else "⚪ 未连接"
            tools = self.agent.mcp_client.list_tools(server_id)
            tool_info = f" ({len(tools)} 工具)" if tools else ""

            workspace_dir = settings.mcp_config_path / server_id
            source = "📁 工作区" if workspace_dir.exists() else "📦 内置"
            output += f"- **{server_id}** {status}{tool_info} [{source}]\n"

        output += (
            "\n**可用操作**:\n"
            "- `call_mcp_tool(server, tool_name, arguments)` 调用工具\n"
            "- `connect_mcp_server(server)` 连接服务器\n"
            "- `add_mcp_server(name, ...)` 添加新服务器\n"
            "- `remove_mcp_server(name)` 移除服务器"
        )
        return output

    async def _get_instructions(self, params: dict) -> str:
        """获取 MCP 使用说明"""
        server = params["server"]
        instructions = self.agent.mcp_catalog.get_server_instructions(server)

        if instructions:
            return f"# MCP 服务器 {server} 使用说明\n\n{instructions}"
        else:
            return f"❌ 未找到服务器 {server} 的使用说明，或服务器不存在"

    def _sync_and_refresh(self, server: str) -> None:
        """同步工具到 catalog 并刷新系统提示"""
        sync_tools_after_connect(server, self.agent.mcp_client, self.agent.mcp_catalog)
        self.agent._mcp_catalog_text = self.agent.mcp_catalog.generate_catalog()
        logger.info("MCP catalog refreshed for %s", server)

    # ==================== 连接管理工具 ====================

    async def _connect_server(self, params: dict) -> str:
        """连接到 MCP 服务器"""
        server = params["server"]
        client = self.agent.mcp_client

        if client.is_connected(server):
            tools = client.list_tools(server)
            return f"✅ 已连接到 {server}（{len(tools)} 个工具可用）"

        if not client.has_server(server):
            return f"❌ 服务器 {server} 未配置。请先用 add_mcp_server 添加或检查名称"

        result = await client.connect(server)
        if result.success:
            self._sync_and_refresh(server)
            tools = client.list_tools(server)
            tool_names = [t.name for t in tools]
            return (
                f"✅ 已连接到 MCP 服务器: {server}\n"
                f"发现 {len(tools)} 个工具: {', '.join(tool_names)}"
            )
        else:
            return (
                f"❌ 连接 MCP 服务器失败: {server}\n"
                f"原因: {result.error}"
            )

    async def _disconnect_server(self, params: dict) -> str:
        """断开 MCP 服务器"""
        server = params["server"]
        client = self.agent.mcp_client

        if not client.is_connected(server):
            return f"⚪ 服务器 {server} 未连接"

        await client.disconnect(server)
        return f"✅ 已断开 MCP 服务器: {server}"

    # ==================== 配置管理工具 ====================

    async def _add_server(self, params: dict) -> str:
        """添加 MCP 服务器配置到工作区"""
        from pathlib import Path

        from ...config import settings
        from ..mcp import VALID_TRANSPORTS

        name = params.get("name", "").strip()
        if not name:
            return "❌ 服务器名称不能为空"

        transport = params.get("transport", "stdio")
        if transport not in VALID_TRANSPORTS:
            return f"❌ 不支持的传输协议: {transport}（支持: {', '.join(sorted(VALID_TRANSPORTS))}）"

        command = params.get("command", "")
        url = params.get("url", "")

        if transport == "stdio" and not command:
            return "❌ stdio 模式需要指定 command 参数"
        if transport in ("streamable_http", "sse") and not url:
            return f"❌ {transport} 模式需要指定 url 参数"

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
            config_base_dir=settings.mcp_config_path,
            search_bases=[settings.project_root, Path.cwd()],
            client=self.agent.mcp_client,
            catalog=self.agent.mcp_catalog,
        )

        self.agent._mcp_catalog_text = self.agent.mcp_catalog.generate_catalog()

        cr = result.get("connect_result") or {}
        if cr.get("connected"):
            tools = self.agent.mcp_client.list_tools(name)
            tool_names = [t.name for t in tools]
            connect_msg = f"\n\n✅ 已自动连接，发现 {len(tools)} 个工具: {', '.join(tool_names)}"
        else:
            connect_msg = (
                f"\n\n⚠️ 自动连接失败: {cr.get('error', '未知')}\n"
                f"配置已保存，可稍后手动调用 `connect_mcp_server(\"{name}\")` 重试"
            )

        return (
            f"✅ 已添加 MCP 服务器: {name}\n"
            f"  传输: {transport}\n"
            f"  配置路径: {result['path']}"
            f"{connect_msg}"
        )

    async def _remove_server(self, params: dict) -> str:
        """移除 MCP 服务器配置"""
        from ...config import settings

        name = params.get("name", "").strip()
        if not name:
            return "❌ 服务器名称不能为空"

        result = await remove_server_from_workspace(
            name,
            config_base_dir=settings.mcp_config_path,
            builtin_dir=settings.mcp_builtin_path,
            client=self.agent.mcp_client,
            catalog=self.agent.mcp_catalog,
        )

        self.agent._mcp_catalog_text = self.agent.mcp_catalog.generate_catalog()

        if result["status"] == "error":
            return f"❌ {result['message']}"
        return f"✅ 已移除 MCP 服务器: {name}"

    async def _reload_servers(self, params: dict) -> str:
        """重新加载所有 MCP 配置

        直接操作全局共享的 mcp_client/mcp_catalog，避免在 pool agent
        上调用 _load_mcp_servers()（那会触发 _start_builtin_mcp_servers 等
        只应在 master agent 上执行的初始化逻辑）。
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

        self.agent._mcp_catalog_text = self.agent.mcp_catalog.generate_catalog()

        return (
            f"✅ MCP 配置已重新加载\n"
            f"  目录中: {counts['catalog_count']} 个服务器\n"
            f"  可连接: {counts['client_count']} 个服务器\n"
            f"  之前已连接的 {counts['previously_connected']} 个服务器已断开\n\n"
            f"使用 `connect_mcp_server(server)` 重新连接"
        )


def create_handler(agent: "Agent"):
    """创建 MCP 处理器"""
    handler = MCPHandler(agent)
    return handler.handle
