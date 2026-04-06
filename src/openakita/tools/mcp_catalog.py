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
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MCPToolInfo:
    """MCP 工具信息"""

    name: str
    description: str
    server: str
    arguments: dict = field(default_factory=dict)


@dataclass
class MCPConfigField:
    """MCP 服务器配置参数声明（SERVER_METADATA.json 中的 configSchema 条目）"""

    key: str
    label: str = ""
    type: str = "text"  # text | secret | number | select | bool | url | path
    required: bool = False
    help: str = ""
    help_url: str = ""
    default: str = ""
    placeholder: str = ""
    options: list[str] = field(default_factory=list)
    when: dict[str, str] = field(default_factory=dict)


@dataclass
class MCPServerInfo:
    """MCP 服务器信息"""

    identifier: str
    name: str
    tools: list[MCPToolInfo] = field(default_factory=list)
    instructions: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"  # "stdio" | "streamable_http" | "sse"
    url: str = ""  # streamable_http / sse 模式使用
    headers: dict[str, str] = field(default_factory=dict)
    auto_connect: bool = False
    enabled: bool = True  # per-server 启用/禁用，默认启用（向后兼容）
    config_dir: str = ""  # 配置文件所在目录（用作 stdio 的 cwd 回退）
    config_schema: list[MCPConfigField] = field(default_factory=list)


_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")
_ENV_FILE_CACHE: dict[str, dict[str, str]] = {}


def _read_nearest_env_values(start_dir: Path) -> dict[str, str]:
    """Read the nearest workspace ``.env`` while walking up from ``start_dir``."""
    current = start_dir
    for _ in range(8):
        env_path = current / ".env"
        if env_path.is_file():
            cache_key = str(env_path.resolve())
            cached = _ENV_FILE_CACHE.get(cache_key)
            if cached is not None:
                return cached
            try:
                from dotenv import dotenv_values

                values = {
                    str(k): str(v)
                    for k, v in dotenv_values(env_path).items()
                    if k and v is not None
                }
                _ENV_FILE_CACHE[cache_key] = values
                return values
            except Exception as e:
                logger.warning("Failed to read MCP env file %s: %s", env_path, e)
                _ENV_FILE_CACHE[cache_key] = {}
                return {}
        parent = current.parent
        if parent == current:
            break
        current = parent
    return {}


def clear_env_file_cache() -> None:
    """Clear the cached .env file values so the next read picks up fresh data."""
    _ENV_FILE_CACHE.clear()


def _resolve_env_vars(value: str, env_values: dict[str, str] | None = None) -> str:
    """Replace ``${VAR_NAME}`` patterns with workspace env or ``os.environ`` values."""
    return _ENV_VAR_RE.sub(
        lambda m: (env_values or {}).get(m.group(1), os.environ.get(m.group(1), "")),
        value,
    )


def _resolve_headers(raw: dict, env_values: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve env-var placeholders in header values, dropping empty ones."""
    resolved: dict[str, str] = {}
    for k, v in raw.items():
        val = _resolve_env_vars(str(v), env_values)
        if val:
            resolved[k] = val
        else:
            logger.warning("MCP header %s resolved to empty (env var not set?), skipping", k)
    return resolved


def _parse_config_schema(raw: list) -> list[MCPConfigField]:
    """Parse ``configSchema`` array from SERVER_METADATA.json into dataclass list."""
    result: list[MCPConfigField] = []
    for item in raw:
        if not isinstance(item, dict) or "key" not in item:
            continue
        result.append(
            MCPConfigField(
                key=item["key"],
                label=item.get("label", ""),
                type=item.get("type", "text"),
                required=bool(item.get("required", False)),
                help=item.get("help", ""),
                help_url=item.get("helpUrl", ""),
                default=str(item.get("default", "")),
                placeholder=item.get("placeholder", ""),
                options=item.get("options") or [],
                when=item.get("when") or {},
            )
        )
    return result


class MCPCatalog:
    """
    MCP 目录

    扫描 MCP 配置目录，生成工具清单用于系统提示注入。
    """

    # MCP 清单模板
    CATALOG_TEMPLATE = """
## MCP Servers (Model Context Protocol)

Use `call_mcp_tool(server, tool_name, arguments)` to call an MCP tool when needed.
Use `connect_mcp_server(server)` to connect a server and discover its tools.

{server_list}
"""

    SERVER_TEMPLATE = """### {server_name} (`{server_id}`)
{tools_list}"""

    SERVER_NO_TOOLS_TEMPLATE = """### {server_name} (`{server_id}`)
- *(Not connected — use `connect_mcp_server("{server_id}")` to discover available tools)*"""

    TOOL_ENTRY_TEMPLATE = "- **{name}**: {description}"

    @staticmethod
    def _safe_format(template: str, **kwargs: str) -> str:
        """str.format that won't crash on {/} in values."""
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError, IndexError) as e:
            logger.warning(
                "[MCPCatalog] str.format failed (template=%r, keys=%s): %s",
                template[:60], list(kwargs.keys()), e,
            )
            return template + " " + " | ".join(f"{k}={v}" for k, v in kwargs.items())

    def __init__(self, mcp_config_dir: Path | None = None):
        """
        初始化 MCP 目录

        Args:
            mcp_config_dir: MCP 配置目录路径 (默认: Cursor 的 mcps 目录)
        """
        self.mcp_config_dir = mcp_config_dir
        self._servers: list[MCPServerInfo] = []
        self._cached_catalog: str | None = None

    def scan_mcp_directory(self, mcp_dir: Path | None = None, clear: bool = False) -> int:
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

        logger.info(
            f"Added {new_count} new MCP servers from {mcp_dir} (total: {len(self._servers)})"
        )
        return new_count

    def register_builtin_server(
        self,
        identifier: str,
        name: str,
        tools: list[dict],
        instructions: str | None = None,
    ) -> None:
        """
        注册内置 MCP 服务器

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

    def _load_server(self, server_dir: Path) -> MCPServerInfo | None:
        """加载单个 MCP 服务器配置"""
        metadata_file = server_dir / "SERVER_METADATA.json"
        if not metadata_file.exists():
            return None

        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            env_values = _read_nearest_env_values(server_dir)

            server_id = metadata.get("serverIdentifier", server_dir.name)
            server_name = metadata.get("serverName", server_id)
            command = metadata.get("command")
            args = metadata.get("args") or []
            env = metadata.get("env") or {}
            # 传输协议：支持 "transport" 字段和 "type" 兼容格式
            transport = metadata.get("transport", "stdio")
            stype = metadata.get("type", "")
            if stype == "streamableHttp":
                transport = "streamable_http"
            elif stype == "sse":
                transport = "sse"
            url = metadata.get("url", "")
            headers = _resolve_headers(metadata.get("headers") or {}, env_values)
            auto_connect = metadata.get("autoConnect", False)
            enabled = metadata.get("enabled", True)

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
                instructions = instructions_file.read_text(encoding="utf-8")

            config_schema = _parse_config_schema(metadata.get("configSchema") or [])

            return MCPServerInfo(
                identifier=server_id,
                name=server_name,
                tools=tools,
                instructions=instructions,
                command=command,
                args=args,
                env=env,
                transport=transport,
                url=url,
                headers=headers,
                auto_connect=auto_connect,
                enabled=enabled,
                config_dir=str(server_dir),
                config_schema=config_schema,
            )

        except Exception as e:
            logger.error(f"Failed to load MCP server {server_dir.name}: {e}")
            return None

    def _load_tool(self, tool_file: Path, server_id: str) -> MCPToolInfo | None:
        """加载单个工具配置"""
        try:
            data = json.loads(tool_file.read_text(encoding="utf-8"))
            # 兼容两种字段名：inputSchema（MCP 规范）和 arguments（旧格式）
            arguments = data.get("inputSchema") or data.get("arguments", {})
            return MCPToolInfo(
                name=data.get("name", tool_file.stem),
                description=data.get("description", ""),
                server=server_id,
                arguments=arguments,
            )
        except Exception as e:
            logger.error(f"Failed to load MCP tool {tool_file}: {e}")
            return None

    def generate_catalog(self) -> str:
        """
        生成 MCP 工具清单

        只包含 enabled=True 的服务器。有工具的展示工具列表，无工具的提示用户连接以发现。

        Returns:
            格式化的 MCP 清单字符串
        """
        enabled_servers = [s for s in self._servers if s.enabled]
        if not enabled_servers:
            if self._servers:
                return "\n## MCP Servers\n\nAll MCP servers are disabled.\n"
            return "\n## MCP Servers\n\nNo MCP servers configured.\n"

        server_sections = []

        for server in enabled_servers:
            if server.tools:
                tool_entries = []
                for tool in server.tools:
                    entry = self._safe_format(
                        self.TOOL_ENTRY_TEMPLATE,
                        name=tool.name,
                        description=tool.description,
                    )
                    tool_entries.append(entry)

                tools_list = "\n".join(tool_entries)

                server_section = self._safe_format(
                    self.SERVER_TEMPLATE,
                    server_name=server.name,
                    server_id=server.identifier,
                    tools_list=tools_list,
                )
            else:
                server_section = self._safe_format(
                    self.SERVER_NO_TOOLS_TEMPLATE,
                    server_name=server.name,
                    server_id=server.identifier,
                )
            server_sections.append(server_section)

        server_list = "\n\n".join(server_sections)

        catalog = self._safe_format(self.CATALOG_TEMPLATE, server_list=server_list)
        self._cached_catalog = catalog

        logger.info(
            f"Generated MCP catalog with {len(enabled_servers)} enabled servers "
            f"(total: {len(self._servers)})"
        )
        return catalog

    def get_catalog(self, refresh: bool = False) -> str:
        """获取 MCP 清单"""
        if refresh or self._cached_catalog is None:
            return self.generate_catalog()
        return self._cached_catalog

    def get_server_instructions(self, server_id: str) -> str | None:
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

    def get_tool_schema(self, server_id: str, tool_name: str) -> dict | None:
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

    def list_enabled_servers(self) -> list[str]:
        """列出所有已启用的服务器标识符"""
        return [s.identifier for s in self._servers if s.enabled]

    def get_server(self, identifier: str) -> MCPServerInfo | None:
        """按 identifier 获取服务器信息"""
        for s in self._servers:
            if s.identifier == identifier:
                return s
        return None

    def has_server(self, identifier: str) -> bool:
        """检查指定 server 是否在 catalog 中（用于调用隔离校验）"""
        return any(s.identifier == identifier for s in self._servers)

    def set_server_enabled(self, identifier: str, enabled: bool) -> bool:
        """设置服务器启用/禁用状态并使缓存失效。返回是否找到。"""
        for s in self._servers:
            if s.identifier == identifier:
                s.enabled = enabled
                self._cached_catalog = None
                return True
        return False

    def clone_filtered(
        self, server_ids: list[str], *, mode: str = "inclusive"
    ) -> "MCPCatalog":
        """创建一个过滤后的 catalog 副本（用于子 Agent per-profile MCP 隔离）。

        Args:
            server_ids: 要包含或排除的服务器 ID 列表
            mode: "inclusive" 只保留列表中的; "exclusive" 排除列表中的
        """
        clone = MCPCatalog(self.mcp_config_dir)
        id_set = set(server_ids)
        for s in self._servers:
            if not s.enabled:
                continue
            if (
                (mode == "inclusive" and s.identifier in id_set)
                or (mode == "exclusive" and s.identifier not in id_set)
            ):
                clone._servers.append(s)
        return clone

    def list_tools(self, server_id: str | None = None) -> list[MCPToolInfo]:
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

    def sync_tools_from_client(self, server_id: str, tools: list[dict], force: bool = False) -> int:
        """
        将运行时发现的工具同步到 catalog（连接后调用）。

        Args:
            server_id: 服务器标识符
            tools: 工具列表，每项需有 name / description / input_schema
            force: 强制覆盖已有工具列表（默认 False，仅在无工具时同步）

        Returns:
            同步的工具数量
        """
        target = None
        for s in self._servers:
            if s.identifier == server_id:
                target = s
                break

        if target is None:
            target = MCPServerInfo(identifier=server_id, name=server_id)
            self._servers.append(target)

        if target.tools and not force:
            return 0

        tool_infos = []
        for t in tools:
            tool_infos.append(MCPToolInfo(
                name=t.get("name", ""),
                description=t.get("description", ""),
                server=server_id,
                arguments=t.get("input_schema") or t.get("inputSchema", {}),
            ))
        target.tools = tool_infos
        self._cached_catalog = None
        logger.info(f"Synced {len(tool_infos)} tools from runtime for MCP server: {server_id}")
        return len(tool_infos)

    def invalidate_cache(self) -> None:
        """使缓存失效"""
        self._cached_catalog = None

    def remove_server(self, identifier: str) -> bool:
        """移除指定服务器并使缓存失效。返回是否找到并移除。"""
        before = len(self._servers)
        self._servers = [s for s in self._servers if s.identifier != identifier]
        removed = len(self._servers) < before
        if removed:
            self._cached_catalog = None
        return removed

    def reset(self) -> None:
        """清空所有服务器并使缓存失效（用于重载配置）"""
        self._servers.clear()
        self._cached_catalog = None

    @property
    def servers(self) -> list[MCPServerInfo]:
        """所有服务器信息（公共只读属性）"""
        return list(self._servers)

    @property
    def server_count(self) -> int:
        """服务器数量"""
        return len(self._servers)

    @property
    def tool_count(self) -> int:
        """工具总数"""
        return sum(len(s.tools) for s in self._servers)


# 全局共享 catalog（与 mcp_client 一样，所有 Agent 共享同一实例）
mcp_catalog = MCPCatalog()


def scan_mcp_servers(mcp_dir: Path) -> MCPCatalog:
    """便捷函数：扫描 MCP 服务器"""
    catalog = MCPCatalog(mcp_dir)
    catalog.scan_mcp_directory()
    return catalog
