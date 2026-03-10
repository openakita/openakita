"""
MCP (Model Context Protocol) 客户端

遵循 MCP 规范 (modelcontextprotocol.io/specification/2025-11-25)
支持连接 MCP 服务器，调用工具、获取资源和提示词

支持的传输协议:
- stdio: 标准输入输出（默认）
- streamable_http: Streamable HTTP (用于 mcp-chrome 等)
- sse: Server-Sent Events (兼容旧版 MCP 服务器)
"""

import asyncio
import contextlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 尝试导入官方 MCP SDK
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False
    logger.warning("MCP SDK not installed. Run: pip install mcp")

# 尝试导入 Streamable HTTP 客户端（MCP SDK >= 1.2.0）
MCP_HTTP_AVAILABLE = False
try:
    from mcp.client.streamable_http import streamablehttp_client

    MCP_HTTP_AVAILABLE = True
except ImportError:
    pass

# 尝试导入 SSE 客户端（兼容旧版 MCP 服务器）
MCP_SSE_AVAILABLE = False
try:
    from mcp.client.sse import sse_client

    MCP_SSE_AVAILABLE = True
except ImportError:
    pass


@dataclass
class MCPTool:
    """MCP 工具"""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


@dataclass
class MCPResource:
    """MCP 资源"""

    uri: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class MCPPrompt:
    """MCP 提示词"""

    name: str
    description: str
    arguments: list[dict] = field(default_factory=list)


VALID_TRANSPORTS = {"stdio", "streamable_http", "sse"}


@dataclass
class MCPServerConfig:
    """MCP 服务器配置"""

    name: str
    command: str = ""  # stdio 模式使用
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    description: str = ""
    transport: str = "stdio"  # "stdio" | "streamable_http" | "sse"
    url: str = ""  # streamable_http / sse 模式使用
    cwd: str = ""  # stdio 模式的工作目录（为空则继承父进程）


@dataclass
class MCPCallResult:
    """MCP 调用结果"""

    success: bool
    data: Any = None
    error: str | None = None


@dataclass
class MCPConnectResult:
    """MCP 连接结果（包含详细错误信息）"""

    success: bool
    error: str | None = None
    tool_count: int = 0


class MCPClient:
    """
    MCP 客户端

    连接 MCP 服务器并调用其功能
    """

    def __init__(self):
        self._servers: dict[str, MCPServerConfig] = {}
        self._connections: dict[str, Any] = {}  # 活跃连接
        self._tools: dict[str, MCPTool] = {}
        self._resources: dict[str, MCPResource] = {}
        self._prompts: dict[str, MCPPrompt] = {}
        self._load_timeouts()

    def add_server(self, config: MCPServerConfig) -> None:
        """添加服务器配置"""
        self._servers[config.name] = config
        logger.info(f"Added MCP server config: {config.name}")

    def load_servers_from_config(self, config_path: Path) -> int:
        """
        从配置文件加载服务器

        配置文件格式 (JSON):
        {
            "mcpServers": {
                "server-name": {
                    "command": "python",
                    "args": ["-m", "my_server"],
                    "env": {}
                }
            }
        }
        """
        if not config_path.exists():
            logger.warning(f"MCP config not found: {config_path}")
            return 0

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {})

            for name, server_data in servers.items():
                transport = server_data.get("transport", "stdio")
                # 兼容多种格式
                stype = server_data.get("type", "")
                if stype == "streamableHttp":
                    transport = "streamable_http"
                elif stype == "sse":
                    transport = "sse"
                config = MCPServerConfig(
                    name=name,
                    command=server_data.get("command", ""),
                    args=server_data.get("args", []),
                    env=server_data.get("env", {}),
                    description=server_data.get("description", ""),
                    transport=transport,
                    url=server_data.get("url", ""),
                )
                self.add_server(config)

            logger.info(f"Loaded {len(servers)} MCP servers from {config_path}")
            return len(servers)

        except Exception as e:
            logger.error(f"Failed to load MCP config: {e}")
            return 0

    async def connect(self, server_name: str) -> MCPConnectResult:
        """
        连接到 MCP 服务器

        支持 stdio、streamable_http、sse 三种传输协议。

        Args:
            server_name: 服务器名称

        Returns:
            MCPConnectResult 包含成功状态、错误详情、发现的工具数
        """
        if not MCP_SDK_AVAILABLE:
            msg = "MCP SDK 未安装，请运行: pip install mcp"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        if server_name not in self._servers:
            msg = f"服务器未配置: {server_name}"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        if server_name in self._connections:
            tool_count = len(self.list_tools(server_name))
            return MCPConnectResult(success=True, tool_count=tool_count)

        config = self._servers[server_name]

        # stdio 模式预检查命令是否存在
        if config.transport == "stdio" and config.command:
            if not self._resolve_command(config):
                msg = (
                    f"启动命令 '{config.command}' 未找到。"
                    f"请确认已安装并在 PATH 中可访问。"
                )
                logger.error(f"MCP connect pre-check failed for {server_name}: {msg}")
                return MCPConnectResult(success=False, error=msg)

        try:
            if config.transport == "streamable_http":
                return await self._connect_streamable_http(server_name, config)
            elif config.transport == "sse":
                return await self._connect_sse(server_name, config)
            else:
                return await self._connect_stdio(server_name, config)

        except BaseException as e:
            msg = f"{type(e).__name__}: {e}"
            logger.error(f"Failed to connect to {server_name}: {msg}")
            return MCPConnectResult(success=False, error=msg)

    @staticmethod
    def _resolve_command(config: MCPServerConfig) -> str | None:
        """在子进程实际使用的 PATH / cwd 下查找命令，避免误判 'not found'。"""
        cmd = config.command

        # 1) 相对路径 + cwd：直接在目标 cwd 下判断文件是否存在
        if config.cwd and (cmd.startswith("./") or cmd.startswith(".\\")):
            candidate = Path(config.cwd) / cmd
            if candidate.is_file():
                return str(candidate.resolve())

        # 2) 用子进程的 env.PATH 查找（用户可能通过 env 配置了自定义 PATH）
        search_path = None
        if config.env:
            search_path = config.env.get("PATH") or config.env.get("Path")

        found = shutil.which(cmd, path=search_path)
        if found:
            return found

        # 3) 如果有 cwd，也在 cwd 下做一次绝对搜索
        if config.cwd:
            candidate = Path(config.cwd) / cmd
            if candidate.is_file():
                return str(candidate.resolve())

        return None

    _CONNECT_TIMEOUT: int = 30
    _CALL_TIMEOUT: int = 60

    def _load_timeouts(self) -> None:
        """从配置加载超时参数（settings → 环境变量 → 默认值）"""
        try:
            from ..config import settings
            self._CONNECT_TIMEOUT = settings.mcp_connect_timeout
            self._CALL_TIMEOUT = settings.mcp_timeout
        except Exception:
            pass

    async def _connect_stdio(self, server_name: str, config: MCPServerConfig) -> MCPConnectResult:
        """通过 stdio 连接到 MCP 服务器"""
        # 连接前二次解析：如果 args 中有相对路径且 cwd 已知，尝试解析
        args = list(config.args)
        if config.cwd:
            cwd_path = Path(config.cwd)
            for i, arg in enumerate(args):
                if not arg.startswith("-") and not Path(arg).is_absolute():
                    candidate = cwd_path / arg
                    if candidate.is_file():
                        args[i] = str(candidate.resolve())

        server_params = StdioServerParameters(
            command=config.command,
            args=args,
            env=config.env or None,
            cwd=config.cwd or None,
        )

        stdio_cm = None
        client_cm = None
        try:
            stdio_cm = stdio_client(server_params)
            read, write = await asyncio.wait_for(
                stdio_cm.__aenter__(), timeout=self._CONNECT_TIMEOUT,
            )

            client_cm = ClientSession(read, write)
            client = await asyncio.wait_for(
                client_cm.__aenter__(), timeout=self._CONNECT_TIMEOUT,
            )
            await asyncio.wait_for(client.initialize(), timeout=self._CONNECT_TIMEOUT)

            await asyncio.wait_for(
                self._discover_capabilities(server_name, client),
                timeout=self._CONNECT_TIMEOUT,
            )

            self._connections[server_name] = {
                "client": client,
                "transport": "stdio",
                "_client_cm": client_cm,
                "_stdio_cm": stdio_cm,
            }
            tool_count = len(self.list_tools(server_name))
            logger.info(f"Connected to MCP server via stdio: {server_name} ({tool_count} tools)")
            return MCPConnectResult(success=True, tool_count=tool_count)
        except asyncio.TimeoutError:
            msg = f"连接超时（{self._CONNECT_TIMEOUT}s）。命令: {config.command} {' '.join(config.args)}"
            logger.error(f"Timeout connecting to {server_name} via stdio")
            await self._cleanup_cms(client_cm, stdio_cm)
            return MCPConnectResult(success=False, error=msg)
        except FileNotFoundError:
            msg = f"启动命令未找到: '{config.command}'。请确认已安装。"
            logger.error(f"Command not found for {server_name}: {config.command}")
            await self._cleanup_cms(client_cm, stdio_cm)
            return MCPConnectResult(success=False, error=msg)
        except BaseException as e:
            msg = f"stdio 连接失败: {type(e).__name__}: {e}"
            logger.error(f"Failed to connect to {server_name} via stdio: {e}")
            await self._cleanup_cms(client_cm, stdio_cm)
            return MCPConnectResult(success=False, error=msg)

    async def _connect_streamable_http(self, server_name: str, config: MCPServerConfig) -> MCPConnectResult:
        """通过 Streamable HTTP 连接到 MCP 服务器"""
        if not MCP_HTTP_AVAILABLE:
            msg = "Streamable HTTP 传输不可用，请升级 MCP SDK: pip install 'mcp>=1.2.0'"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        if not config.url:
            msg = f"未配置 URL（streamable_http 模式必填）: {server_name}"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        http_cm = None
        client_cm = None
        try:
            http_cm = streamablehttp_client(url=config.url)
            read, write, _ = await asyncio.wait_for(
                http_cm.__aenter__(), timeout=self._CONNECT_TIMEOUT,
            )

            client_cm = ClientSession(read, write)
            client = await asyncio.wait_for(
                client_cm.__aenter__(), timeout=self._CONNECT_TIMEOUT,
            )
            await asyncio.wait_for(client.initialize(), timeout=self._CONNECT_TIMEOUT)

            await asyncio.wait_for(
                self._discover_capabilities(server_name, client),
                timeout=self._CONNECT_TIMEOUT,
            )

            self._connections[server_name] = {
                "client": client,
                "transport": "streamable_http",
                "_client_cm": client_cm,
                "_http_cm": http_cm,
            }
            tool_count = len(self.list_tools(server_name))
            logger.info(f"Connected to MCP server via streamable HTTP: {server_name} ({config.url}, {tool_count} tools)")
            return MCPConnectResult(success=True, tool_count=tool_count)
        except asyncio.TimeoutError:
            msg = f"HTTP 连接超时（{self._CONNECT_TIMEOUT}s）。URL: {config.url}"
            logger.error(f"Timeout connecting to {server_name} via streamable HTTP")
            await self._cleanup_cms(client_cm, http_cm)
            return MCPConnectResult(success=False, error=msg)
        except BaseException as e:
            msg = f"HTTP 连接失败: {type(e).__name__}: {e}"
            logger.error(f"Failed to connect to {server_name} via streamable HTTP: {e}")
            await self._cleanup_cms(client_cm, http_cm)
            return MCPConnectResult(success=False, error=msg)

    async def _connect_sse(self, server_name: str, config: MCPServerConfig) -> MCPConnectResult:
        """通过 SSE (Server-Sent Events) 连接到 MCP 服务器"""
        if not MCP_SSE_AVAILABLE:
            msg = "SSE 传输不可用，请升级 MCP SDK: pip install 'mcp>=1.2.0'"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        if not config.url:
            msg = f"未配置 URL（sse 模式必填）: {server_name}"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        sse_cm = None
        client_cm = None
        try:
            sse_cm = sse_client(url=config.url)
            read, write = await asyncio.wait_for(
                sse_cm.__aenter__(), timeout=self._CONNECT_TIMEOUT,
            )

            client_cm = ClientSession(read, write)
            client = await asyncio.wait_for(
                client_cm.__aenter__(), timeout=self._CONNECT_TIMEOUT,
            )
            await asyncio.wait_for(client.initialize(), timeout=self._CONNECT_TIMEOUT)

            await asyncio.wait_for(
                self._discover_capabilities(server_name, client),
                timeout=self._CONNECT_TIMEOUT,
            )

            self._connections[server_name] = {
                "client": client,
                "transport": "sse",
                "_client_cm": client_cm,
                "_sse_cm": sse_cm,
            }
            tool_count = len(self.list_tools(server_name))
            logger.info(f"Connected to MCP server via SSE: {server_name} ({config.url}, {tool_count} tools)")
            return MCPConnectResult(success=True, tool_count=tool_count)
        except asyncio.TimeoutError:
            msg = f"SSE 连接超时（{self._CONNECT_TIMEOUT}s）。URL: {config.url}"
            logger.error(f"Timeout connecting to {server_name} via SSE")
            await self._cleanup_cms(client_cm, sse_cm)
            return MCPConnectResult(success=False, error=msg)
        except BaseException as e:
            msg = f"SSE 连接失败: {type(e).__name__}: {e}"
            logger.error(f"Failed to connect to {server_name} via SSE: {e}")
            await self._cleanup_cms(client_cm, sse_cm)
            return MCPConnectResult(success=False, error=msg)

    @staticmethod
    async def _cleanup_cms(*cms: Any) -> None:
        """安全清理 context managers"""
        for cm in cms:
            if cm is None:
                continue
            try:
                await cm.__aexit__(None, None, None)
            except BaseException:
                pass

    async def _discover_capabilities(self, server_name: str, client: Any) -> None:
        """发现 MCP 服务器的能力（工具、资源、提示词）"""
        # 获取工具
        tools_result = await client.list_tools()
        for tool in tools_result.tools:
            self._tools[f"{server_name}:{tool.name}"] = MCPTool(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema or {},
            )

        # 获取资源（可选）
        with contextlib.suppress(Exception):
            resources_result = await client.list_resources()
            for resource in resources_result.resources:
                self._resources[f"{server_name}:{resource.uri}"] = MCPResource(
                    uri=resource.uri,
                    name=resource.name,
                    description=resource.description or "",
                    mime_type=resource.mimeType or "",
                )

        # 获取提示词（可选）
        with contextlib.suppress(Exception):
            prompts_result = await client.list_prompts()
            for prompt in prompts_result.prompts:
                self._prompts[f"{server_name}:{prompt.name}"] = MCPPrompt(
                    name=prompt.name,
                    description=prompt.description or "",
                    arguments=prompt.arguments or [],
                )

    async def disconnect(self, server_name: str) -> None:
        """断开服务器连接

        MCP SDK 的 stdio_client 内部使用 anyio cancel scope。如果 disconnect()
        与 connect() 不在同一个 asyncio task 中执行（例如 connect 在初始化 task，
        disconnect 在工具执行 task），__aexit__ 会触发:
            RuntimeError: Attempted to exit cancel scope in a different task
        该错误会在异步生成器清理阶段传播到事件循环，导致整个后端进程崩溃。

        修复策略:
        1. 对 stdio 连接先终止子进程，避免管道断裂问题
        2. 将 CM 清理放到独立后台 task 中执行并隔离异常
        3. 主调用方只等待有限时间，不会因清理失败而阻塞或崩溃
        """
        if server_name in self._connections:
            conn = self._connections.pop(server_name)

            # 对 stdio 连接，先终止子进程再清理 CM
            if conn.get("transport") == "stdio":
                await self._terminate_stdio_subprocess(conn.get("_stdio_cm"))

            # 在独立后台 task 中清理 context managers，
            # 隔离 anyio cancel scope 跨任务错误
            task = asyncio.create_task(
                self._isolated_cm_cleanup(server_name, conn),
                name=f"mcp-cleanup-{server_name}",
            )
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.debug(
                    "MCP cleanup for %s timed out or was cancelled", server_name,
                )
            except BaseException:
                logger.debug(
                    "MCP cleanup for %s raised unexpected error (ignored)",
                    server_name, exc_info=True,
                )
            finally:
                if task.done() and not task.cancelled():
                    with contextlib.suppress(BaseException):
                        task.result()
                elif not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task

            # 清理该服务器的工具/资源/提示词
            self._tools = {
                k: v for k, v in self._tools.items() if not k.startswith(f"{server_name}:")
            }
            self._resources = {
                k: v for k, v in self._resources.items() if not k.startswith(f"{server_name}:")
            }
            self._prompts = {
                k: v for k, v in self._prompts.items() if not k.startswith(f"{server_name}:")
            }
            logger.info(f"Disconnected from MCP server: {server_name}")

    @staticmethod
    async def _terminate_stdio_subprocess(stdio_cm: Any) -> None:
        """终止 stdio_client 管理的子进程。

        通过 async generator 的 frame locals 访问子进程句柄并直接终止，
        避免后续 __aexit__ 时因管道断裂导致 Windows ProactorEventLoop 异常。
        """
        if stdio_cm is None:
            return
        try:
            frame = getattr(stdio_cm, "ag_frame", None)
            if frame is None:
                return
            proc = frame.f_locals.get("process")
            if proc is None:
                return
            if hasattr(proc, "terminate"):
                proc.terminate()
                # 等待子进程退出，超时则强杀
                if hasattr(proc, "wait"):
                    try:
                        wait_coro = proc.wait()
                        if asyncio.iscoroutine(wait_coro):
                            await asyncio.wait_for(wait_coro, timeout=2)
                    except (asyncio.TimeoutError, ProcessLookupError):
                        with contextlib.suppress(Exception):
                            if hasattr(proc, "kill"):
                                proc.kill()
                    except BaseException:
                        pass
        except Exception:
            pass

    @staticmethod
    async def _isolated_cm_cleanup(server_name: str, conn: dict) -> None:
        """在独立 task 中逐个清理 context managers。

        即使 anyio 抛出 RuntimeError（跨任务 cancel scope），
        也不会传播到主事件循环。
        """
        for cm_key in ("_client_cm", "_stdio_cm", "_http_cm", "_sse_cm"):
            cm = conn.get(cm_key)
            if cm is None:
                continue
            try:
                await asyncio.wait_for(
                    cm.__aexit__(None, None, None), timeout=5,
                )
            except BaseException:
                logger.debug(
                    "MCP %s cleanup failed for %s (ignored)",
                    cm_key, server_name, exc_info=True,
                )

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict,
    ) -> MCPCallResult:
        """
        调用 MCP 工具

        Args:
            server_name: 服务器名称
            tool_name: 工具名称
            arguments: 参数

        Returns:
            MCPCallResult
        """
        if not MCP_SDK_AVAILABLE:
            return MCPCallResult(
                success=False,
                error="MCP SDK not available. Install with: pip install mcp",
            )

        if server_name not in self._connections:
            return MCPCallResult(
                success=False,
                error=f"Not connected to server: {server_name}",
            )

        tool_key = f"{server_name}:{tool_name}"
        if tool_key not in self._tools:
            return MCPCallResult(
                success=False,
                error=f"Tool not found: {tool_name}",
            )

        try:
            conn = self._connections[server_name]
            client = conn.get("client") if isinstance(conn, dict) else conn
            if client is None:
                return MCPCallResult(success=False, error=f"Invalid connection for server: {server_name}")

            result = await asyncio.wait_for(
                client.call_tool(tool_name, arguments),
                timeout=self._CALL_TIMEOUT,
            )

            content = []
            for item in result.content:
                if hasattr(item, "text"):
                    content.append(item.text)
                elif hasattr(item, "data"):
                    content.append(item.data)

            return MCPCallResult(
                success=True,
                data=content[0] if len(content) == 1 else content,
            )

        except BaseException as e:
            logger.error(f"MCP tool call failed ({server_name}:{tool_name}): {type(e).__name__}: {e}")
            return MCPCallResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    async def read_resource(
        self,
        server_name: str,
        uri: str,
    ) -> MCPCallResult:
        """
        读取 MCP 资源

        Args:
            server_name: 服务器名称
            uri: 资源 URI

        Returns:
            MCPCallResult
        """
        if not MCP_SDK_AVAILABLE:
            return MCPCallResult(success=False, error="MCP SDK not available")

        if server_name not in self._connections:
            return MCPCallResult(success=False, error=f"Not connected: {server_name}")

        try:
            conn = self._connections[server_name]
            client = conn.get("client") if isinstance(conn, dict) else conn
            if client is None:
                return MCPCallResult(success=False, error=f"Invalid connection for server: {server_name}")
            result = await asyncio.wait_for(
                client.read_resource(uri), timeout=self._CALL_TIMEOUT,
            )

            content = []
            for item in result.contents:
                if hasattr(item, "text"):
                    content.append(item.text)
                elif hasattr(item, "blob"):
                    content.append(item.blob)

            return MCPCallResult(
                success=True,
                data=content[0] if len(content) == 1 else content,
            )

        except BaseException as e:
            logger.error(f"MCP read_resource failed ({server_name}:{uri}): {type(e).__name__}: {e}")
            return MCPCallResult(success=False, error=f"{type(e).__name__}: {e}")

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        arguments: dict | None = None,
    ) -> MCPCallResult:
        """
        获取 MCP 提示词

        Args:
            server_name: 服务器名称
            prompt_name: 提示词名称
            arguments: 参数

        Returns:
            MCPCallResult
        """
        if not MCP_SDK_AVAILABLE:
            return MCPCallResult(success=False, error="MCP SDK not available")

        if server_name not in self._connections:
            return MCPCallResult(success=False, error=f"Not connected: {server_name}")

        try:
            conn = self._connections[server_name]
            client = conn.get("client") if isinstance(conn, dict) else conn
            if client is None:
                return MCPCallResult(success=False, error=f"Invalid connection for server: {server_name}")
            result = await asyncio.wait_for(
                client.get_prompt(prompt_name, arguments or {}),
                timeout=self._CALL_TIMEOUT,
            )

            messages = []
            for msg in result.messages:
                messages.append(
                    {
                        "role": msg.role,
                        "content": msg.content.text
                        if hasattr(msg.content, "text")
                        else str(msg.content),
                    }
                )

            return MCPCallResult(success=True, data=messages)

        except BaseException as e:
            logger.error(f"MCP get_prompt failed ({server_name}:{prompt_name}): {type(e).__name__}: {e}")
            return MCPCallResult(success=False, error=f"{type(e).__name__}: {e}")

    def list_servers(self) -> list[str]:
        """列出所有配置的服务器"""
        return list(self._servers.keys())

    def list_connected(self) -> list[str]:
        """列出已连接的服务器"""
        return list(self._connections.keys())

    def list_tools(self, server_name: str | None = None) -> list[MCPTool]:
        """列出工具"""
        if server_name:
            prefix = f"{server_name}:"
            return [t for k, t in self._tools.items() if k.startswith(prefix)]
        return list(self._tools.values())

    def list_resources(self, server_name: str | None = None) -> list[MCPResource]:
        """列出资源"""
        if server_name:
            prefix = f"{server_name}:"
            return [r for k, r in self._resources.items() if k.startswith(prefix)]
        return list(self._resources.values())

    def list_prompts(self, server_name: str | None = None) -> list[MCPPrompt]:
        """列出提示词"""
        if server_name:
            prefix = f"{server_name}:"
            return [p for k, p in self._prompts.items() if k.startswith(prefix)]
        return list(self._prompts.values())

    def get_tool_schemas(self) -> list[dict]:
        """获取所有工具的 LLM 调用 schema"""
        schemas = []
        for key, tool in self._tools.items():
            server_name = key.split(":")[0]
            schemas.append(
                {
                    "name": f"mcp_{server_name}_{tool.name}".replace("-", "_"),
                    "description": f"[MCP:{server_name}] {tool.description}",
                    "input_schema": tool.input_schema,
                }
            )
        return schemas


# 全局客户端
mcp_client = MCPClient()


# 便捷函数
async def connect_mcp_server(name: str) -> MCPConnectResult:
    """连接 MCP 服务器"""
    return await mcp_client.connect(name)


async def call_mcp_tool(server: str, tool: str, args: dict) -> MCPCallResult:
    """调用 MCP 工具"""
    return await mcp_client.call_tool(server, tool, args)


def get_mcp_tool_schemas() -> list[dict]:
    """获取 MCP 工具 schema"""
    return mcp_client.get_tool_schemas()
