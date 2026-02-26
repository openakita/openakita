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

import json
import logging
import shutil
from typing import TYPE_CHECKING, Any

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

        if server not in self.agent.mcp_client.list_connected():
            connected = await self.agent.mcp_client.connect(server)
            if not connected:
                return f"❌ 无法连接到 MCP 服务器: {server}"

        result = await self.agent.mcp_client.call_tool(server, mcp_tool_name, arguments)

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

            # 判断来源
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

    # ==================== 连接管理工具 ====================

    async def _connect_server(self, params: dict) -> str:
        """连接到 MCP 服务器"""
        server = params["server"]

        if server in self.agent.mcp_client.list_connected():
            tools = self.agent.mcp_client.list_tools(server)
            return f"✅ 已连接到 {server}（{len(tools)} 个工具可用）"

        if server not in self.agent.mcp_client.list_servers():
            return f"❌ 服务器 {server} 未配置。请先用 add_mcp_server 添加或检查名称"

        success = await self.agent.mcp_client.connect(server)
        if success:
            tools = self.agent.mcp_client.list_tools(server)
            tool_names = [t.name for t in tools]

            if tools:
                tool_dicts = [
                    {"name": t.name, "description": t.description,
                     "input_schema": t.input_schema}
                    for t in tools
                ]
                self.agent.mcp_catalog.sync_tools_from_client(server, tool_dicts)
                self.agent._mcp_catalog_text = self.agent.mcp_catalog.generate_catalog()

            return (
                f"✅ 已连接到 MCP 服务器: {server}\n"
                f"发现 {len(tools)} 个工具: {', '.join(tool_names)}"
            )
        else:
            return f"❌ 连接 MCP 服务器失败: {server}\n请检查服务器配置和网络连接"

    async def _disconnect_server(self, params: dict) -> str:
        """断开 MCP 服务器"""
        server = params["server"]

        if server not in self.agent.mcp_client.list_connected():
            return f"⚪ 服务器 {server} 未连接"

        await self.agent.mcp_client.disconnect(server)
        return f"✅ 已断开 MCP 服务器: {server}"

    # ==================== 配置管理工具 ====================

    async def _add_server(self, params: dict) -> str:
        """添加 MCP 服务器配置到工作区"""
        from ...config import settings

        name = params.get("name", "").strip()
        if not name:
            return "❌ 服务器名称不能为空"

        transport = params.get("transport", "stdio")
        command = params.get("command", "")
        args = params.get("args", [])
        env = params.get("env", {})
        url = params.get("url", "")
        description = params.get("description", name)
        instructions_text = params.get("instructions", "")
        auto_connect = params.get("auto_connect", False)

        if transport == "stdio" and not command:
            return "❌ stdio 模式需要指定 command 参数"
        if transport == "streamable_http" and not url:
            return "❌ streamable_http 模式需要指定 url 参数"

        # 写入到工作区 data/mcp/servers/{name}/
        server_dir = settings.mcp_config_path / name
        server_dir.mkdir(parents=True, exist_ok=True)

        metadata = {
            "serverIdentifier": name,
            "serverName": description,
            "command": command,
            "args": args,
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

        if instructions_text:
            instructions_file = server_dir / "INSTRUCTIONS.md"
            instructions_file.write_text(instructions_text, encoding="utf-8")

        # 热加载: 注册到 catalog 和 client
        self.agent.mcp_catalog.scan_mcp_directory(settings.mcp_config_path)
        self.agent.mcp_catalog.invalidate_cache()

        from ..mcp import MCPServerConfig
        self.agent.mcp_client.add_server(MCPServerConfig(
            name=name,
            command=command,
            args=args,
            env=env,
            description=description,
            transport=transport,
            url=url,
        ))

        # 刷新系统提示中的 MCP 清单
        self.agent._mcp_catalog_text = self.agent.mcp_catalog.generate_catalog()

        return (
            f"✅ 已添加 MCP 服务器: {name}\n"
            f"  传输: {transport}\n"
            f"  配置路径: {server_dir}\n\n"
            f"使用 `connect_mcp_server(\"{name}\")` 连接并发现工具"
        )

    async def _remove_server(self, params: dict) -> str:
        """移除 MCP 服务器配置"""
        from ...config import settings

        name = params.get("name", "").strip()
        if not name:
            return "❌ 服务器名称不能为空"

        # 只允许删除工作区中的配置
        server_dir = settings.mcp_config_path / name
        builtin_dir = settings.mcp_builtin_path / name

        if not server_dir.exists():
            if builtin_dir.exists():
                return f"❌ {name} 是内置 MCP 服务器，不能删除。可在 .env 中禁用 MCP"
            return f"❌ 未找到 MCP 服务器: {name}"

        # 先断开连接
        if name in self.agent.mcp_client.list_connected():
            await self.agent.mcp_client.disconnect(name)

        # 删除配置文件
        shutil.rmtree(server_dir, ignore_errors=True)

        # 从 client 中移除（服务器配置 + 残留工具索引）
        self.agent.mcp_client._servers.pop(name, None)
        self.agent.mcp_client._connections.pop(name, None)
        prefix = f"{name}:"
        for key in [k for k in self.agent.mcp_client._tools if k.startswith(prefix)]:
            del self.agent.mcp_client._tools[key]

        # 从 catalog 中移除
        self.agent.mcp_catalog._servers = [
            s for s in self.agent.mcp_catalog._servers
            if s.identifier != name
        ]
        self.agent.mcp_catalog.invalidate_cache()

        # 刷新系统提示
        self.agent._mcp_catalog_text = self.agent.mcp_catalog.generate_catalog()

        return f"✅ 已移除 MCP 服务器: {name}"

    async def _reload_servers(self, params: dict) -> str:
        """重新加载所有 MCP 配置"""
        # 断开所有连接
        connected = list(self.agent.mcp_client.list_connected())
        for server_name in connected:
            try:
                await self.agent.mcp_client.disconnect(server_name)
            except Exception as e:
                logger.warning(f"Failed to disconnect {server_name}: {e}")

        # 清空现有配置（含可能因断连失败而残留的连接）
        self.agent.mcp_client._connections.clear()
        self.agent.mcp_client._servers.clear()
        self.agent.mcp_client._tools.clear()
        self.agent.mcp_client._resources.clear()
        self.agent.mcp_client._prompts.clear()
        self.agent.mcp_catalog._servers.clear()
        self.agent.mcp_catalog.invalidate_cache()

        # 重新加载
        await self.agent._load_mcp_servers()

        catalog_count = self.agent.mcp_catalog.server_count
        client_count = len(self.agent.mcp_client.list_servers())

        return (
            f"✅ MCP 配置已重新加载\n"
            f"  目录中: {catalog_count} 个服务器\n"
            f"  可连接: {client_count} 个服务器\n"
            f"  之前已连接的 {len(connected)} 个服务器已断开\n\n"
            f"使用 `connect_mcp_server(server)` 重新连接"
        )


def create_handler(agent: "Agent"):
    """创建 MCP 处理器"""
    handler = MCPHandler(agent)
    return handler.handle
