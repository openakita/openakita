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
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# anyio 连接断开相关异常（MCP SDK 底层依赖 anyio）
_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (ConnectionError, EOFError, OSError)
try:
    import anyio
    _CONNECTION_ERRORS = (
        anyio.ClosedResourceError,
        anyio.BrokenResourceError,
        anyio.EndOfStream,
        ConnectionError,
        EOFError,
    )
except ImportError:
    pass

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
    headers: dict[str, str] = field(default_factory=dict)
    cwd: str = ""  # stdio 模式的工作目录（为空则继承父进程）


@dataclass
class MCPCallResult:
    """MCP 调用结果"""

    success: bool
    data: Any = None
    error: str | None = None
    reconnected: bool = False


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
                    headers=server_data.get("headers", {}),
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
        # 打包模式下 python -m openakita.* 会在 _connect_stdio 中被适配为冻结主程序，跳过预检
        if config.transport == "stdio" and config.command:
            if not self._adapt_bundled_module_command(config) and not self._resolve_command(config):
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
        from ..utils.path_helper import which_command

        cmd = config.command

        # 1) 相对路径 + cwd：直接在目标 cwd 下判断文件是否存在
        if config.cwd and (cmd.startswith("./") or cmd.startswith(".\\")):
            candidate = Path(config.cwd) / cmd
            if candidate.is_file():
                return str(candidate.resolve())

        # 2) 用子进程的 env.PATH 查找（含 macOS login shell PATH 回退）
        search_path = None
        if config.env:
            search_path = config.env.get("PATH") or config.env.get("Path")

        found = which_command(cmd, extra_path=search_path)
        if found:
            return found

        # 3) 如果有 cwd，也在 cwd 下做一次绝对搜索
        if config.cwd:
            candidate = Path(config.cwd) / cmd
            if candidate.is_file():
                return str(candidate.resolve())

        return None

    @staticmethod
    def _adapt_bundled_module_command(
        config: MCPServerConfig,
    ) -> tuple[str, list[str]] | None:
        """打包模式下，将 ``python -m openakita.*`` 适配为冻结主程序子命令。

        PyInstaller 冻结的 python.exe 是裸解释器，无法 import 冻结在主程序中的模块。
        此方法将命令替换为 ``sys.executable run-mcp-module <module>``，
        让冻结主程序自身作为 MCP 服务器的宿主进程。

        Returns:
            (command, args) 如果需要适配；否则 None。
        """
        from ..runtime_env import IS_FROZEN

        if not (
            IS_FROZEN
            and config.command in ("python", "python3")
            and len(config.args) >= 2
            and config.args[0] == "-m"
            and config.args[1].startswith("openakita.")
        ):
            return None
        return (sys.executable, ["run-mcp-module", config.args[1], *config.args[2:]])

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
        adapted = self._adapt_bundled_module_command(config)
        if adapted:
            command, args = adapted
            logger.info(
                "Bundled mode: adapted MCP command for %s: %s %s",
                server_name, command, " ".join(args),
            )
        else:
            command = config.command
            args = list(config.args)
            # 连接前二次解析：如果 args 中有相对路径且 cwd 已知，尝试解析
            if config.cwd:
                cwd_path = Path(config.cwd)
                for i, arg in enumerate(args):
                    if not arg.startswith("-") and not Path(arg).is_absolute():
                        candidate = cwd_path / arg
                        if candidate.is_file():
                            args[i] = str(candidate.resolve())

        # macOS GUI 应用的 PATH 不含 Homebrew/NVM/Volta 等用户工具路径，
        # 需要通过 login shell 获取完整 PATH 传递给 MCP 子进程
        from ..utils.path_helper import get_macos_enriched_env
        subprocess_env: dict | None = dict(config.env) if config.env else None
        subprocess_env = get_macos_enriched_env(subprocess_env)

        # Windows PyInstaller: _internal/ 目录下的 python.exe 是裸解释器,
        # 会影响外部脚本的 python 命令解析 — 从 PATH 中移除
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            if subprocess_env is None:
                subprocess_env = dict(os.environ)
            internal_dir = str(Path(sys.executable).parent / "_internal")
            for path_key in ("PATH", "Path"):
                if path_key in subprocess_env:
                    subprocess_env[path_key] = os.pathsep.join(
                        p for p in subprocess_env[path_key].split(os.pathsep)
                        if not p.startswith(internal_dir)
                    )
                    break

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=subprocess_env,
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
            stderr_hint = self._try_capture_stdio_stderr(stdio_cm)
            msg = (
                f"连接超时（{self._CONNECT_TIMEOUT}s）。"
                f"命令: {command} {' '.join(args)}{stderr_hint}"
            )
            logger.error("Timeout connecting to %s via stdio%s", server_name, stderr_hint)
            await self._cleanup_cms(client_cm, stdio_cm)
            return MCPConnectResult(success=False, error=msg)
        except FileNotFoundError:
            msg = f"启动命令未找到: '{command}'。请确认已安装。"
            logger.error(f"Command not found for {server_name}: {command}")
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
        _managed_http_client = None
        try:
            kwargs: dict[str, Any] = {"url": config.url}
            if config.headers:
                import httpx as _httpx
                _managed_http_client = _httpx.AsyncClient(
                    headers=config.headers,
                    timeout=_httpx.Timeout(self._CONNECT_TIMEOUT),
                )
                kwargs["http_client"] = _managed_http_client
            http_cm = streamablehttp_client(**kwargs)
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
                "_http_client": _managed_http_client,
            }
            tool_count = len(self.list_tools(server_name))
            logger.info(f"Connected to MCP server via streamable HTTP: {server_name} ({config.url}, {tool_count} tools)")
            return MCPConnectResult(success=True, tool_count=tool_count)
        except asyncio.TimeoutError:
            msg = f"HTTP 连接超时（{self._CONNECT_TIMEOUT}s）。URL: {config.url}"
            logger.error(f"Timeout connecting to {server_name} via streamable HTTP")
            await self._cleanup_cms(client_cm, http_cm)
            if _managed_http_client:
                await _managed_http_client.aclose()
            return MCPConnectResult(success=False, error=msg)
        except BaseException as e:
            msg = f"HTTP 连接失败: {type(e).__name__}: {e}"
            logger.error(f"Failed to connect to {server_name} via streamable HTTP: {e}")
            await self._cleanup_cms(client_cm, http_cm)
            if _managed_http_client:
                await _managed_http_client.aclose()
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
            sse_cm = sse_client(url=config.url, headers=config.headers or None)
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
    def _try_capture_stdio_stderr(stdio_cm: Any) -> str:
        """Try to read stderr from the stdio subprocess for diagnostic hints."""
        if stdio_cm is None:
            return ""
        try:
            frame = getattr(stdio_cm, "ag_frame", None)
            if frame is None:
                return ""
            proc = frame.f_locals.get("process")
            if proc is None or not hasattr(proc, "stderr") or proc.stderr is None:
                return ""
            stderr_pipe = proc.stderr
            # Non-blocking read of available bytes
            if hasattr(stderr_pipe, "_buffer"):
                data = bytes(stderr_pipe._buffer)
            elif hasattr(stderr_pipe, "read"):
                import asyncio
                try:
                    data = stderr_pipe.read(2048) if not asyncio.iscoroutinefunction(
                        getattr(stderr_pipe, "read", None)
                    ) else b""
                except Exception:
                    data = b""
            else:
                return ""
            if data:
                text = data.decode("utf-8", errors="replace").strip()[:500]
                return f"\n子进程 stderr: {text}"
        except Exception:
            pass
        return ""

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
        http_client = conn.get("_http_client")
        if http_client is not None:
            try:
                await http_client.aclose()
            except BaseException:
                pass

    @staticmethod
    def _extract_content(items: list) -> list:
        """从 MCP 响应中提取所有 content 块的文本/数据表示。"""
        content = []
        for item in items:
            if hasattr(item, "text"):
                content.append(item.text)
            elif hasattr(item, "data"):
                content.append(item.data)
            elif hasattr(item, "resource"):
                content.append(f"[resource: {getattr(item.resource, 'uri', item.resource)}]")
            else:
                content.append(str(item))
        return content

    @staticmethod
    def _is_connection_error(exc: BaseException) -> bool:
        """判断异常是否表示底层连接已断开（服务端关闭 / 管道断裂等）"""
        if isinstance(exc, _CONNECTION_ERRORS):
            return True
        name = type(exc).__name__
        if name in ("ClosedResourceError", "BrokenResourceError", "EndOfStream"):
            return True
        return False

    async def _reconnect(self, server_name: str) -> bool:
        """清理死连接并重新建立连接，成功返回 True"""
        logger.info("Attempting to reconnect MCP server: %s", server_name)

        old_conn = self._connections.pop(server_name, None)
        if old_conn:
            if old_conn.get("transport") == "stdio":
                await self._terminate_stdio_subprocess(old_conn.get("_stdio_cm"))
            task = asyncio.create_task(
                self._isolated_cm_cleanup(server_name, old_conn),
                name=f"mcp-reconnect-cleanup-{server_name}",
            )
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except BaseException:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task

        if server_name not in self._servers:
            return False

        # 先清理旧的工具/资源/提示词注册，让 _discover_capabilities 从干净状态写入。
        # 如果重连失败，这些条目本来也不可用（连接已死）。
        prefix = f"{server_name}:"
        self._tools = {k: v for k, v in self._tools.items() if not k.startswith(prefix)}
        self._resources = {k: v for k, v in self._resources.items() if not k.startswith(prefix)}
        self._prompts = {k: v for k, v in self._prompts.items() if not k.startswith(prefix)}

        result = await self.connect(server_name)
        if result.success:
            logger.info(
                "Reconnected to MCP server: %s (%d tools)",
                server_name, result.tool_count,
            )
        else:
            logger.warning("Reconnect failed for %s: %s", server_name, result.error)
        return result.success

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

        did_reconnect = False
        for attempt in range(2):
            try:
                conn = self._connections.get(server_name)
                if conn is None:
                    return MCPCallResult(
                        success=False, error=f"Not connected to server: {server_name}",
                    )
                client = conn.get("client") if isinstance(conn, dict) else conn
                if client is None:
                    return MCPCallResult(
                        success=False, error=f"Invalid connection for server: {server_name}",
                    )

                result = await asyncio.wait_for(
                    client.call_tool(tool_name, arguments),
                    timeout=self._CALL_TIMEOUT,
                )

                content = self._extract_content(result.content)

                if getattr(result, "isError", False):
                    error_text = "\n".join(str(c) for c in content) if content else "Unknown error"
                    logger.warning(
                        "MCP tool %s:%s returned isError=true: %s",
                        server_name, tool_name, error_text[:500],
                    )
                    return MCPCallResult(
                        success=False,
                        error=error_text,
                        reconnected=did_reconnect,
                    )

                return MCPCallResult(
                    success=True,
                    data=content[0] if len(content) == 1 else content,
                    reconnected=did_reconnect,
                )

            except BaseException as e:
                if attempt == 0 and self._is_connection_error(e):
                    logger.warning(
                        "MCP connection lost for %s:%s (%s), reconnecting…",
                        server_name, tool_name, type(e).__name__,
                    )
                    if await self._reconnect(server_name):
                        did_reconnect = True
                        continue
                logger.error(
                    "MCP tool call failed (%s:%s): %s: %s",
                    server_name, tool_name, type(e).__name__, e,
                )
                return MCPCallResult(success=False, error=f"{type(e).__name__}: {e}")

        return MCPCallResult(success=False, error="Unexpected: retry loop exhausted")

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

        for attempt in range(2):
            try:
                conn = self._connections.get(server_name)
                if conn is None:
                    return MCPCallResult(
                        success=False, error=f"Not connected: {server_name}",
                    )
                client = conn.get("client") if isinstance(conn, dict) else conn
                if client is None:
                    return MCPCallResult(
                        success=False, error=f"Invalid connection for server: {server_name}",
                    )
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
                if attempt == 0 and self._is_connection_error(e):
                    logger.warning(
                        "MCP connection lost for %s (read_resource %s), reconnecting…",
                        server_name, uri,
                    )
                    if await self._reconnect(server_name):
                        continue
                logger.error(
                    "MCP read_resource failed (%s:%s): %s: %s",
                    server_name, uri, type(e).__name__, e,
                )
                return MCPCallResult(success=False, error=f"{type(e).__name__}: {e}")

        return MCPCallResult(success=False, error="Unexpected: retry loop exhausted")

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

        for attempt in range(2):
            try:
                conn = self._connections.get(server_name)
                if conn is None:
                    return MCPCallResult(
                        success=False, error=f"Not connected: {server_name}",
                    )
                client = conn.get("client") if isinstance(conn, dict) else conn
                if client is None:
                    return MCPCallResult(
                        success=False, error=f"Invalid connection for server: {server_name}",
                    )
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
                if attempt == 0 and self._is_connection_error(e):
                    logger.warning(
                        "MCP connection lost for %s (get_prompt %s), reconnecting…",
                        server_name, prompt_name,
                    )
                    if await self._reconnect(server_name):
                        continue
                logger.error(
                    "MCP get_prompt failed (%s:%s): %s: %s",
                    server_name, prompt_name, type(e).__name__, e,
                )
                return MCPCallResult(success=False, error=f"{type(e).__name__}: {e}")

        return MCPCallResult(success=False, error="Unexpected: retry loop exhausted")

    # ==================== 公共状态查询 / 管理 ====================

    def has_server(self, name: str) -> bool:
        """检查服务器是否已配置"""
        return name in self._servers

    def is_connected(self, name: str) -> bool:
        """检查服务器是否已连接"""
        return name in self._connections

    def get_server_config(self, name: str) -> MCPServerConfig | None:
        """获取服务器配置（只读）"""
        return self._servers.get(name)

    def remove_server(self, name: str) -> None:
        """移除服务器配置及其关联的工具/资源/提示词（不断开连接，需先调 disconnect）"""
        self._servers.pop(name, None)
        self._connections.pop(name, None)
        prefix = f"{name}:"
        self._tools = {k: v for k, v in self._tools.items() if not k.startswith(prefix)}
        self._resources = {k: v for k, v in self._resources.items() if not k.startswith(prefix)}
        self._prompts = {k: v for k, v in self._prompts.items() if not k.startswith(prefix)}

    async def reset(self) -> None:
        """断开所有连接并清空全部状态（用于重载配置）"""
        for name in list(self._connections):
            try:
                await self.disconnect(name)
            except Exception as e:
                logger.warning("Failed to disconnect %s during reset: %s", name, e)
        self._servers.clear()
        self._connections.clear()
        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()

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
