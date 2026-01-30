"""
MCP 目录 (MCP Catalog)

遵循 Model Context Protocol 规范的渐进式披露:
- Level 1: MCP 服务器和工具清单 - 在系统提示中提供
- Level 2: 工具详细参数 - 调用时加载
- Level 3: INSTRUCTIONS.md - 复杂操作时加载

在 Agent 启动时扫描 MCP 配置目录，生成工具清单注入系统提示。
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MCPToolInfo:
    """MCP 工具信息"""
    name: str
    description: str
    server: str
    arguments: dict = field(default_factory=dict)


@dataclass
class MCPServerInfo:
    """MCP 服务器信息"""
    identifier: str
    name: str
    tools: list[MCPToolInfo] = field(default_factory=list)
    instructions: Optional[str] = None


class MCPCatalog:
    """
    MCP 目录
    
    扫描 MCP 配置目录，生成工具清单用于系统提示注入。
    """
    
    # MCP 清单模板
    CATALOG_TEMPLATE = """
## MCP Servers (Model Context Protocol)

The following MCP servers and tools are available for external integrations:

{server_list}

### How to Use MCP Tools

Use `CallMcpTool` with `server` and `toolName` parameters. Example:
```
CallMcpTool(server="mysql-test", toolName="execute_query", arguments={{"sql": "SELECT * FROM users"}})
```
"""

    SERVER_TEMPLATE = """### {server_name} (`{server_id}`)
{tools_list}"""

    TOOL_ENTRY_TEMPLATE = "- **{name}**: {description}"
    
    def __init__(self, mcp_config_dir: Optional[Path] = None):
        """
        初始化 MCP 目录
        
        Args:
            mcp_config_dir: MCP 配置目录路径 (默认: Cursor 的 mcps 目录)
        """
        self.mcp_config_dir = mcp_config_dir
        self._servers: list[MCPServerInfo] = []
        self._cached_catalog: Optional[str] = None
    
    def scan_mcp_directory(self, mcp_dir: Optional[Path] = None, clear: bool = False) -> int:
        """
        扫描 MCP 配置目录
        
        Args:
            mcp_dir: MCP 目录路径
            clear: 是否清空已有服务器 (默认 False，追加模式)
        
        Returns:
            本次发现的服务器数量
        """
        mcp_dir = mcp_dir or self.mcp_config_dir
        if not mcp_dir or not mcp_dir.exists():
            logger.warning(f"MCP config directory not found: {mcp_dir}")
            return 0
        
        if clear:
            self._servers = []
        
        # 已存在的服务器 ID (用于去重)
        existing_ids = {s.identifier for s in self._servers}
        new_count = 0
        
        for server_dir in mcp_dir.iterdir():
            if not server_dir.is_dir():
                continue
            
            server_info = self._load_server(server_dir)
            if server_info:
                # 去重: 如果已存在相同 ID 的服务器，跳过 (项目本地优先)
                if server_info.identifier not in existing_ids:
                    self._servers.append(server_info)
                    existing_ids.add(server_info.identifier)
                    new_count += 1
                else:
                    logger.debug(f"Skipped duplicate MCP server: {server_info.identifier}")
        
        logger.info(f"Added {new_count} new MCP servers from {mcp_dir} (total: {len(self._servers)})")
        return new_count
    
    def register_builtin_server(
        self,
        identifier: str,
        name: str,
        tools: list[dict],
        instructions: Optional[str] = None,
    ) -> None:
        """
        注册内置 MCP 服务器 (如 browser-use)
        
        Args:
            identifier: 服务器 ID
            name: 服务器名称
            tools: 工具定义列表 [{"name": ..., "description": ..., "inputSchema": ...}]
            instructions: 使用说明 (可选)
        """
        # 检查是否已存在
        existing_ids = {s.identifier for s in self._servers}
        if identifier in existing_ids:
            logger.debug(f"Builtin server already registered: {identifier}")
            return
        
        # 转换工具格式
        tool_infos = []
        for tool in tools:
            tool_info = MCPToolInfo(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                server=identifier,
                arguments=tool.get("inputSchema", {}),
            )
            tool_infos.append(tool_info)
        
        # 创建服务器信息
        server_info = MCPServerInfo(
            identifier=identifier,
            name=name,
            tools=tool_infos,
            instructions=instructions,
        )
        
        self._servers.append(server_info)
        logger.info(f"Registered builtin MCP server: {identifier} ({len(tool_infos)} tools)")
    
    def _load_server(self, server_dir: Path) -> Optional[MCPServerInfo]:
        """加载单个 MCP 服务器配置"""
        metadata_file = server_dir / "SERVER_METADATA.json"
        if not metadata_file.exists():
            return None
        
        try:
            metadata = json.loads(metadata_file.read_text(encoding='utf-8'))
            
            server_id = metadata.get('serverIdentifier', server_dir.name)
            server_name = metadata.get('serverName', server_id)
            
            # 加载工具
            tools = []
            tools_dir = server_dir / "tools"
            if tools_dir.exists():
                for tool_file in tools_dir.glob("*.json"):
                    tool_info = self._load_tool(tool_file, server_id)
                    if tool_info:
                        tools.append(tool_info)
            
            # 加载指令
            instructions = None
            instructions_file = server_dir / "INSTRUCTIONS.md"
            if instructions_file.exists():
                instructions = instructions_file.read_text(encoding='utf-8')
            
            return MCPServerInfo(
                identifier=server_id,
                name=server_name,
                tools=tools,
                instructions=instructions,
            )
            
        except Exception as e:
            logger.error(f"Failed to load MCP server {server_dir.name}: {e}")
            return None
    
    def _load_tool(self, tool_file: Path, server_id: str) -> Optional[MCPToolInfo]:
        """加载单个工具配置"""
        try:
            data = json.loads(tool_file.read_text(encoding='utf-8'))
            return MCPToolInfo(
                name=data.get('name', tool_file.stem),
                description=data.get('description', ''),
                server=server_id,
                arguments=data.get('arguments', {}),
            )
        except Exception as e:
            logger.error(f"Failed to load MCP tool {tool_file}: {e}")
            return None
    
    def generate_catalog(self) -> str:
        """
        生成 MCP 工具清单
        
        Returns:
            格式化的 MCP 清单字符串
        """
        if not self._servers:
            return "\n## MCP Servers\n\nNo MCP servers configured.\n"
        
        server_sections = []
        
        for server in self._servers:
            if not server.tools:
                continue
            
            # 生成工具列表
            tool_entries = []
            for tool in server.tools:
                desc = tool.description[:100] + "..." if len(tool.description) > 100 else tool.description
                entry = self.TOOL_ENTRY_TEMPLATE.format(
                    name=tool.name,
                    description=desc,
                )
                tool_entries.append(entry)
            
            tools_list = "\n".join(tool_entries)
            
            server_section = self.SERVER_TEMPLATE.format(
                server_name=server.name,
                server_id=server.identifier,
                tools_list=tools_list,
            )
            server_sections.append(server_section)
        
        server_list = "\n\n".join(server_sections)
        
        catalog = self.CATALOG_TEMPLATE.format(server_list=server_list)
        self._cached_catalog = catalog
        
        logger.info(f"Generated MCP catalog with {len(self._servers)} servers")
        return catalog
    
    def get_catalog(self, refresh: bool = False) -> str:
        """获取 MCP 清单"""
        if refresh or self._cached_catalog is None:
            return self.generate_catalog()
        return self._cached_catalog
    
    def get_server_instructions(self, server_id: str) -> Optional[str]:
        """
        获取服务器的完整指令 (Level 2)
        
        Args:
            server_id: 服务器标识符
        
        Returns:
            INSTRUCTIONS.md 内容
        """
        for server in self._servers:
            if server.identifier == server_id:
                return server.instructions
        return None
    
    def get_tool_schema(self, server_id: str, tool_name: str) -> Optional[dict]:
        """
        获取工具的完整 schema
        
        Args:
            server_id: 服务器标识符
            tool_name: 工具名称
        
        Returns:
            工具参数 schema
        """
        for server in self._servers:
            if server.identifier == server_id:
                for tool in server.tools:
                    if tool.name == tool_name:
                        return tool.arguments
        return None
    
    def list_servers(self) -> list[str]:
        """列出所有服务器标识符"""
        return [s.identifier for s in self._servers]
    
    def list_tools(self, server_id: Optional[str] = None) -> list[MCPToolInfo]:
        """列出工具"""
        if server_id:
            for server in self._servers:
                if server.identifier == server_id:
                    return server.tools
            return []
        
        all_tools = []
        for server in self._servers:
            all_tools.extend(server.tools)
        return all_tools
    
    def invalidate_cache(self) -> None:
        """使缓存失效"""
        self._cached_catalog = None
    
    @property
    def server_count(self) -> int:
        """服务器数量"""
        return len(self._servers)
    
    @property
    def tool_count(self) -> int:
        """工具总数"""
        return sum(len(s.tools) for s in self._servers)


def scan_mcp_servers(mcp_dir: Path) -> MCPCatalog:
    """便捷函数：扫描 MCP 服务器"""
    catalog = MCPCatalog(mcp_dir)
    catalog.scan_mcp_directory()
    return catalog
