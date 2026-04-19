"""
MCP (Model Context Protocol) client

Follows the MCP specification (modelcontextprotocol.io/specification/2025-11-25).
Supports connecting to MCP servers, calling tools, and fetching resources and prompts.

Supported transports:
- stdio: standard input/output (default)
- streamable_http: Streamable HTTP (used by mcp-chrome, etc.)
- sse: Server-Sent Events (compatible with legacy MCP servers)
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

# anyio connection-loss exceptions (the MCP SDK relies on anyio under the hood)
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

# ── MCP SDK import (supports lazy retry + auto-install) ──

MCP_SDK_AVAILABLE = False
MCP_HTTP_AVAILABLE = False
MCP_SSE_AVAILABLE = False
_mcp_import_attempted = False
_mcp_auto_install_attempted = False


def _try_import_mcp() -> bool:
    """Try to import the MCP SDK and update the global availability flags. Returns True on success."""
    global MCP_SDK_AVAILABLE, MCP_HTTP_AVAILABLE, MCP_SSE_AVAILABLE, _mcp_import_attempted
    _mcp_import_attempted = True

    try:
        from mcp import ClientSession, StdioServerParameters  # noqa: F401
        from mcp.client.stdio import stdio_client  # noqa: F401

        MCP_SDK_AVAILABLE = True
    except ImportError:
        MCP_SDK_AVAILABLE = False
        logger.warning("MCP SDK not installed. Run: pip install mcp")
        return False

    try:
        from mcp.client.streamable_http import streamablehttp_client  # noqa: F401

        MCP_HTTP_AVAILABLE = True
    except ImportError:
        pass

    try:
        from mcp.client.sse import sse_client  # noqa: F401

        MCP_SSE_AVAILABLE = True
    except ImportError:
        pass

    return True


def _auto_install_mcp() -> bool:
    """Try to auto-install the MCP SDK. Returns whether the install succeeded."""
    global _mcp_auto_install_attempted
    if _mcp_auto_install_attempted:
        return False
    _mcp_auto_install_attempted = True

    logger.info("[MCP] MCP SDK not found, attempting auto-install...")
    try:
        import subprocess

        exe = sys.executable
        mirrors = [
            ("pypi", [exe, "-m", "pip", "install", "mcp", "--quiet"]),
            (
                "tuna",
                [
                    exe,
                    "-m",
                    "pip",
                    "install",
                    "mcp",
                    "--quiet",
                    "-i",
                    "https://pypi.tuna.tsinghua.edu.cn/simple/",
                ],
            ),
            (
                "aliyun",
                [
                    exe,
                    "-m",
                    "pip",
                    "install",
                    "mcp",
                    "--quiet",
                    "-i",
                    "https://mirrors.aliyun.com/pypi/simple/",
                ],
            ),
        ]
        for label, cmd in mirrors:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    logger.info("[MCP] Auto-installed MCP SDK via %s", label)
                    return _try_import_mcp()
            except Exception as e:
                logger.debug("[MCP] Install via %s failed: %s", label, e)
                continue
        logger.warning("[MCP] Auto-install failed for all mirrors")
        return False
    except Exception as e:
        logger.warning("[MCP] Auto-install error: %s", e)
        return False


def ensure_mcp_sdk() -> bool:
    """Ensure the MCP SDK is available. Import on first call; fall back to auto-install on failure."""
    if MCP_SDK_AVAILABLE:
        return True
    if not _mcp_import_attempted:
        if _try_import_mcp():
            return True
    if not _mcp_auto_install_attempted:
        return _auto_install_mcp()
    return False


# Initial import attempt
_try_import_mcp()

# Backward-compatible try/except placeholders
try:
    if MCP_SDK_AVAILABLE:
        from mcp import ClientSession, StdioServerParameters  # noqa: F811
        from mcp.client.stdio import stdio_client  # noqa: F811
except ImportError:
    pass

try:
    if MCP_HTTP_AVAILABLE:
        from mcp.client.streamable_http import streamablehttp_client  # noqa: F811
except ImportError:
    pass

try:
    if MCP_SSE_AVAILABLE:
        from mcp.client.sse import sse_client  # noqa: F811
except ImportError:
    pass


@dataclass
class MCPTool:
    """MCP tool"""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


@dataclass
class MCPResource:
    """MCP resource"""

    uri: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class MCPPrompt:
    """MCP prompt"""

    name: str
    description: str
    arguments: list[dict] = field(default_factory=list)


VALID_TRANSPORTS = {"stdio", "streamable_http", "sse"}


@dataclass
class MCPServerConfig:
    """MCP server configuration"""

    name: str
    command: str = ""  # used in stdio mode
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    description: str = ""
    transport: str = "stdio"  # "stdio" | "streamable_http" | "sse"
    url: str = ""  # used in streamable_http / sse mode
    headers: dict[str, str] = field(default_factory=dict)
    cwd: str = ""  # working directory for stdio mode (empty inherits the parent process)


@dataclass
class MCPCallResult:
    """MCP call result"""

    success: bool
    data: Any = None
    error: str | None = None
    reconnected: bool = False


@dataclass
class MCPConnectResult:
    """MCP connect result (includes detailed error information)"""

    success: bool
    error: str | None = None
    tool_count: int = 0


class MCPClient:
    """
    MCP client

    Connects to MCP servers and invokes their capabilities.
    """

    def __init__(self):
        self._servers: dict[str, MCPServerConfig] = {}
        self._connections: dict[str, Any] = {}  # active connections
        self._tools: dict[str, MCPTool] = {}
        self._resources: dict[str, MCPResource] = {}
        self._prompts: dict[str, MCPPrompt] = {}
        self._load_timeouts()

    def add_server(self, config: MCPServerConfig) -> None:
        """Add a server configuration"""
        self._servers[config.name] = config
        logger.info(f"Added MCP server config: {config.name}")

    def load_servers_from_config(self, config_path: Path) -> int:
        """
        Load servers from a configuration file.

        Config file format (JSON):
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
                # Handle multiple formats
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
        Connect to an MCP server.

        Supports stdio, streamable_http, and sse transports.

        Args:
            server_name: server name

        Returns:
            MCPConnectResult with success flag, error details, and discovered tool count.
        """
        if not MCP_SDK_AVAILABLE:
            if not ensure_mcp_sdk():
                msg = (
                    "MCP SDK is not installed and auto-install failed.\n"
                    "Please install it manually in the OpenAkita Python environment:\n"
                    f"  {sys.executable} -m pip install mcp\n"
                    "Restart OpenAkita after installation for the change to take effect."
                )
                logger.error(msg)
                return MCPConnectResult(success=False, error=msg)
            logger.info("[MCP] SDK became available after lazy install, re-importing...")

        if server_name not in self._servers:
            msg = f"Server not configured: {server_name}"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        if server_name in self._connections:
            tool_count = len(self.list_tools(server_name))
            return MCPConnectResult(success=True, tool_count=tool_count)

        config = self._servers[server_name]

        # Pre-check that the stdio command exists.
        # ``python -m openakita.*`` will be adapted to the current runtime in _connect_stdio
        # to avoid accidentally using the system Python, which may fail to import bundled modules.
        if config.transport == "stdio" and config.command:
            if not self._adapt_openakita_module_command(config) and not self._resolve_command(
                config
            ):
                msg = f"Startup command '{config.command}' not found. Make sure it is installed and on PATH."
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
        """Look up the command using the PATH / cwd the child process will actually use, avoiding false 'not found'."""
        from ..utils.path_helper import which_command

        cmd = config.command

        # 1) Relative path + cwd: check whether the file exists under the target cwd directly
        if config.cwd and (cmd.startswith("./") or cmd.startswith(".\\")):
            candidate = Path(config.cwd) / cmd
            if candidate.is_file():
                return str(candidate.resolve())

        # 2) Search using the child's env.PATH (with macOS login shell PATH fallback)
        search_path = None
        if config.env:
            search_path = config.env.get("PATH") or config.env.get("Path")

        found = which_command(cmd, extra_path=search_path)
        if found:
            return found

        # 3) If cwd is set, also perform an absolute search under cwd
        if config.cwd:
            candidate = Path(config.cwd) / cmd
            if candidate.is_file():
                return str(candidate.resolve())

        return None

    @staticmethod
    def _adapt_openakita_module_command(
        config: MCPServerConfig,
    ) -> tuple[str, list[str]] | None:
        """Adapt ``python -m openakita.*`` to the current OpenAkita runtime environment.

        - Packaged builds: use ``sys.executable run-mcp-module <module>``,
          so the frozen main program itself hosts the MCP server, avoiding a bare
          interpreter that cannot import the bundled modules.
        - Development: use the current virtualenv's Python interpreter instead of
          the system Python on PATH, so ``python -m openakita.*`` doesn't land in
          the wrong environment.

        Returns:
            (command, args) when adaptation is needed; otherwise None.
        """
        from ..runtime_env import IS_FROZEN, get_python_executable

        if not (
            config.command in ("python", "python3")
            and len(config.args) >= 2
            and config.args[0] == "-m"
            and config.args[1].startswith("openakita.")
        ):
            return None

        if IS_FROZEN:
            return (sys.executable, ["run-mcp-module", config.args[1], *config.args[2:]])

        py = get_python_executable() or sys.executable
        py_path = Path(py)
        if py_path.name.lower() not in ("python.exe", "python3.exe", "python", "python3"):
            for candidate_name in ("python.exe", "python3.exe", "python", "python3"):
                candidate = py_path.with_name(candidate_name)
                if candidate.exists():
                    py = str(candidate)
                    break

        return (py, ["-m", config.args[1], *config.args[2:]])

    _CONNECT_TIMEOUT: int = 30
    _CALL_TIMEOUT: int = 60

    def _load_timeouts(self) -> None:
        """Load timeout parameters from config (settings → environment variable → default)."""
        try:
            from ..config import settings

            self._CONNECT_TIMEOUT = settings.mcp_connect_timeout
            self._CALL_TIMEOUT = settings.mcp_timeout
        except Exception:
            pass

    async def _connect_stdio(self, server_name: str, config: MCPServerConfig) -> MCPConnectResult:
        """Connect to an MCP server via stdio."""
        adapted = self._adapt_openakita_module_command(config)
        if adapted:
            command, args = adapted
            logger.info(
                "Adapted MCP command for %s: %s %s",
                server_name,
                command,
                " ".join(args),
            )
        else:
            command = config.command
            args = list(config.args)
            # Secondary resolution before connecting: if args contain relative paths and cwd is known, try to resolve them
            if config.cwd:
                cwd_path = Path(config.cwd)
                for i, arg in enumerate(args):
                    if not arg.startswith("-") and not Path(arg).is_absolute():
                        candidate = cwd_path / arg
                        if candidate.is_file():
                            args[i] = str(candidate.resolve())

        # On macOS, GUI apps inherit a PATH that excludes Homebrew/NVM/Volta
        # and other user tool paths; fetch the full PATH via a login shell
        # and pass it to the MCP subprocess.
        from ..utils.path_helper import get_macos_enriched_env

        subprocess_env: dict | None = dict(config.env) if config.env else None
        subprocess_env = get_macos_enriched_env(subprocess_env)

        # Windows PyInstaller: the python.exe under _internal/ is a bare interpreter
        # that can interfere with external scripts' python resolution — remove it from PATH.
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            if subprocess_env is None:
                subprocess_env = dict(os.environ)
            internal_dir = str(Path(sys.executable).parent / "_internal")
            for path_key in ("PATH", "Path"):
                if path_key in subprocess_env:
                    subprocess_env[path_key] = os.pathsep.join(
                        p
                        for p in subprocess_env[path_key].split(os.pathsep)
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
                stdio_cm.__aenter__(),
                timeout=self._CONNECT_TIMEOUT,
            )

            client_cm = ClientSession(read, write)
            client = await asyncio.wait_for(
                client_cm.__aenter__(),
                timeout=self._CONNECT_TIMEOUT,
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
        except (asyncio.TimeoutError, TimeoutError):
            stderr_hint = self._try_capture_stdio_stderr(stdio_cm)
            msg = (
                f"Connection timed out ({self._CONNECT_TIMEOUT}s). "
                f"Command: {command} {' '.join(args)}{stderr_hint}"
            )
            logger.error("Timeout connecting to %s via stdio%s", server_name, stderr_hint)
            await self._cleanup_cms(client_cm, stdio_cm)
            return MCPConnectResult(success=False, error=msg)
        except FileNotFoundError:
            msg = f"Startup command not found: '{command}'. Make sure it is installed."
            logger.error(f"Command not found for {server_name}: {command}")
            await self._cleanup_cms(client_cm, stdio_cm)
            return MCPConnectResult(success=False, error=msg)
        except BaseException as e:
            stderr_hint = self._try_capture_stdio_stderr(stdio_cm)
            msg = f"stdio connection failed: {type(e).__name__}: {e}{stderr_hint}"
            logger.error(f"Failed to connect to {server_name} via stdio: {e}")
            await self._cleanup_cms(client_cm, stdio_cm)
            return MCPConnectResult(success=False, error=msg)

    async def _connect_streamable_http(
        self, server_name: str, config: MCPServerConfig
    ) -> MCPConnectResult:
        """Connect to an MCP server over Streamable HTTP."""
        if not MCP_HTTP_AVAILABLE:
            ensure_mcp_sdk()
        if not MCP_HTTP_AVAILABLE:
            msg = "Streamable HTTP transport is not available. Please upgrade the MCP SDK: pip install 'mcp>=1.2.0'"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        if not config.url:
            msg = f"URL not configured (required for streamable_http mode): {server_name}"
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
                http_cm.__aenter__(),
                timeout=self._CONNECT_TIMEOUT,
            )

            client_cm = ClientSession(read, write)
            client = await asyncio.wait_for(
                client_cm.__aenter__(),
                timeout=self._CONNECT_TIMEOUT,
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
            logger.info(
                f"Connected to MCP server via streamable HTTP: {server_name} ({config.url}, {tool_count} tools)"
            )
            return MCPConnectResult(success=True, tool_count=tool_count)
        except (asyncio.TimeoutError, TimeoutError):
            msg = f"HTTP connection timed out ({self._CONNECT_TIMEOUT}s). URL: {config.url}"
            logger.error(f"Timeout connecting to {server_name} via streamable HTTP")
            await self._cleanup_cms(client_cm, http_cm)
            if _managed_http_client:
                await _managed_http_client.aclose()
            return MCPConnectResult(success=False, error=msg)
        except BaseException as e:
            msg = f"HTTP connection failed: {type(e).__name__}: {e}"
            logger.error(f"Failed to connect to {server_name} via streamable HTTP: {e}")
            await self._cleanup_cms(client_cm, http_cm)
            if _managed_http_client:
                await _managed_http_client.aclose()
            return MCPConnectResult(success=False, error=msg)

    async def _connect_sse(self, server_name: str, config: MCPServerConfig) -> MCPConnectResult:
        """Connect to an MCP server over SSE (Server-Sent Events)."""
        if not MCP_SSE_AVAILABLE:
            ensure_mcp_sdk()
        if not MCP_SSE_AVAILABLE:
            msg = "SSE transport is not available. Please upgrade the MCP SDK: pip install 'mcp>=1.2.0'"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        if not config.url:
            msg = f"URL not configured (required for sse mode): {server_name}"
            logger.error(msg)
            return MCPConnectResult(success=False, error=msg)

        sse_cm = None
        client_cm = None
        try:
            sse_cm = sse_client(url=config.url, headers=config.headers or None)
            read, write = await asyncio.wait_for(
                sse_cm.__aenter__(),
                timeout=self._CONNECT_TIMEOUT,
            )

            client_cm = ClientSession(read, write)
            client = await asyncio.wait_for(
                client_cm.__aenter__(),
                timeout=self._CONNECT_TIMEOUT,
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
            logger.info(
                f"Connected to MCP server via SSE: {server_name} ({config.url}, {tool_count} tools)"
            )
            return MCPConnectResult(success=True, tool_count=tool_count)
        except (asyncio.TimeoutError, TimeoutError):
            msg = f"SSE connection timed out ({self._CONNECT_TIMEOUT}s). URL: {config.url}"
            logger.error(f"Timeout connecting to {server_name} via SSE")
            await self._cleanup_cms(client_cm, sse_cm)
            return MCPConnectResult(success=False, error=msg)
        except BaseException as e:
            msg = f"SSE connection failed: {type(e).__name__}: {e}"
            logger.error(f"Failed to connect to {server_name} via SSE: {e}")
            await self._cleanup_cms(client_cm, sse_cm)
            return MCPConnectResult(success=False, error=msg)

    @staticmethod
    async def _cleanup_cms(*cms: Any) -> None:
        """Safely clean up context managers."""
        for cm in cms:
            if cm is None:
                continue
            try:
                await cm.__aexit__(None, None, None)
            except BaseException:
                pass

    async def _discover_capabilities(self, server_name: str, client: Any) -> None:
        """Discover an MCP server's capabilities (tools, resources, prompts)."""
        # Fetch tools
        tools_result = await client.list_tools()
        for tool in tools_result.tools:
            self._tools[f"{server_name}:{tool.name}"] = MCPTool(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema or {},
            )

        # Fetch resources (optional)
        with contextlib.suppress(Exception):
            resources_result = await client.list_resources()
            for resource in resources_result.resources:
                self._resources[f"{server_name}:{resource.uri}"] = MCPResource(
                    uri=resource.uri,
                    name=resource.name,
                    description=resource.description or "",
                    mime_type=resource.mimeType or "",
                )

        # Fetch prompts (optional)
        with contextlib.suppress(Exception):
            prompts_result = await client.list_prompts()
            for prompt in prompts_result.prompts:
                self._prompts[f"{server_name}:{prompt.name}"] = MCPPrompt(
                    name=prompt.name,
                    description=prompt.description or "",
                    arguments=prompt.arguments or [],
                )

    async def disconnect(self, server_name: str) -> None:
        """Disconnect from a server.

        The MCP SDK's stdio_client uses an anyio cancel scope internally. If
        disconnect() and connect() are not executed in the same asyncio task
        (for example, connect runs in an init task while disconnect runs in a
        tool-execution task), __aexit__ raises:
            RuntimeError: Attempted to exit cancel scope in a different task
        That error propagates out of async-generator cleanup into the event
        loop and crashes the entire backend process.

        Mitigation:
        1. For stdio connections, terminate the subprocess first to avoid broken-pipe issues.
        2. Run CM cleanup on an isolated background task so exceptions are contained.
        3. The caller only waits a bounded time and will not hang or crash on cleanup failure.
        """
        if server_name in self._connections:
            conn = self._connections.pop(server_name)

            # For stdio connections, terminate the subprocess before cleaning up the CM
            if conn.get("transport") == "stdio":
                await self._terminate_stdio_subprocess(conn.get("_stdio_cm"))

            # Clean up context managers on an isolated background task
            # to contain anyio cross-task cancel-scope errors.
            task = asyncio.create_task(
                self._isolated_cm_cleanup(server_name, conn),
                name=f"mcp-cleanup-{server_name}",
            )
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (TimeoutError, asyncio.CancelledError):
                logger.debug(
                    "MCP cleanup for %s timed out or was cancelled",
                    server_name,
                )
            except BaseException:
                logger.debug(
                    "MCP cleanup for %s raised unexpected error (ignored)",
                    server_name,
                    exc_info=True,
                )
            finally:
                if task.done() and not task.cancelled():
                    with contextlib.suppress(BaseException):
                        task.result()
                elif not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task

            # Clean up this server's tools/resources/prompts
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
        """Terminate the subprocess managed by stdio_client.

        Access the subprocess handle via the async generator's frame locals and
        terminate it directly, preventing broken-pipe errors in Windows
        ProactorEventLoop during subsequent __aexit__.
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
                # Wait for the subprocess to exit; force-kill on timeout
                if hasattr(proc, "wait"):
                    try:
                        wait_coro = proc.wait()
                        if asyncio.iscoroutine(wait_coro):
                            await asyncio.wait_for(wait_coro, timeout=2)
                    except (TimeoutError, ProcessLookupError):
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
                    data = (
                        stderr_pipe.read(2048)
                        if not asyncio.iscoroutinefunction(getattr(stderr_pipe, "read", None))
                        else b""
                    )
                except Exception:
                    data = b""
            else:
                return ""
            if data:
                text = data.decode("utf-8", errors="replace").strip()[:500]
                return f"\nSubprocess stderr: {text}"
        except Exception:
            pass
        return ""

    @staticmethod
    async def _isolated_cm_cleanup(server_name: str, conn: dict) -> None:
        """Clean up context managers one at a time on a dedicated task.

        Even if anyio raises RuntimeError (cross-task cancel scope),
        the error does not propagate to the main event loop.
        """
        for cm_key in ("_client_cm", "_stdio_cm", "_http_cm", "_sse_cm"):
            cm = conn.get(cm_key)
            if cm is None:
                continue
            try:
                await asyncio.wait_for(
                    cm.__aexit__(None, None, None),
                    timeout=5,
                )
            except BaseException:
                logger.debug(
                    "MCP %s cleanup failed for %s (ignored)",
                    cm_key,
                    server_name,
                    exc_info=True,
                )
        http_client = conn.get("_http_client")
        if http_client is not None:
            try:
                await http_client.aclose()
            except BaseException:
                pass

    @staticmethod
    def _extract_content(items: list) -> list:
        """Extract text/data representations of all content blocks from an MCP response."""
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
        """Check whether the exception indicates the underlying connection is gone (server closed / broken pipe, etc.)."""
        if isinstance(exc, _CONNECTION_ERRORS):
            return True
        name = type(exc).__name__
        if name in ("ClosedResourceError", "BrokenResourceError", "EndOfStream"):
            return True
        return False

    async def _reconnect(self, server_name: str) -> bool:
        """Clean up a dead connection and reconnect. Returns True on success."""
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

        # Clear the old tool/resource/prompt registrations so _discover_capabilities starts clean.
        # If the reconnect fails, these entries would have been unusable anyway (connection is dead).
        prefix = f"{server_name}:"
        self._tools = {k: v for k, v in self._tools.items() if not k.startswith(prefix)}
        self._resources = {k: v for k, v in self._resources.items() if not k.startswith(prefix)}
        self._prompts = {k: v for k, v in self._prompts.items() if not k.startswith(prefix)}

        result = await self.connect(server_name)
        if result.success:
            logger.info(
                "Reconnected to MCP server: %s (%d tools)",
                server_name,
                result.tool_count,
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
        Call an MCP tool.

        Args:
            server_name: server name
            tool_name: tool name
            arguments: arguments

        Returns:
            MCPCallResult
        """
        if not MCP_SDK_AVAILABLE:
            if not ensure_mcp_sdk():
                return MCPCallResult(
                    success=False,
                    error=(
                        f"MCP SDK is not installed and auto-install failed. Please run: {sys.executable} -m pip install mcp"
                    ),
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
                        success=False,
                        error=f"Not connected to server: {server_name}",
                    )
                client = conn.get("client") if isinstance(conn, dict) else conn
                if client is None:
                    return MCPCallResult(
                        success=False,
                        error=f"Invalid connection for server: {server_name}",
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
                        server_name,
                        tool_name,
                        error_text[:500],
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
                        server_name,
                        tool_name,
                        type(e).__name__,
                    )
                    if await self._reconnect(server_name):
                        did_reconnect = True
                        continue
                logger.error(
                    "MCP tool call failed (%s:%s): %s: %s",
                    server_name,
                    tool_name,
                    type(e).__name__,
                    e,
                )
                return MCPCallResult(success=False, error=f"{type(e).__name__}: {e}")

        return MCPCallResult(success=False, error="Unexpected: retry loop exhausted")

    async def read_resource(
        self,
        server_name: str,
        uri: str,
    ) -> MCPCallResult:
        """
        Read an MCP resource.

        Args:
            server_name: server name
            uri: resource URI

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
                        success=False,
                        error=f"Not connected: {server_name}",
                    )
                client = conn.get("client") if isinstance(conn, dict) else conn
                if client is None:
                    return MCPCallResult(
                        success=False,
                        error=f"Invalid connection for server: {server_name}",
                    )
                result = await asyncio.wait_for(
                    client.read_resource(uri),
                    timeout=self._CALL_TIMEOUT,
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
                        server_name,
                        uri,
                    )
                    if await self._reconnect(server_name):
                        continue
                logger.error(
                    "MCP read_resource failed (%s:%s): %s: %s",
                    server_name,
                    uri,
                    type(e).__name__,
                    e,
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
        Fetch an MCP prompt.

        Args:
            server_name: server name
            prompt_name: prompt name
            arguments: arguments

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
                        success=False,
                        error=f"Not connected: {server_name}",
                    )
                client = conn.get("client") if isinstance(conn, dict) else conn
                if client is None:
                    return MCPCallResult(
                        success=False,
                        error=f"Invalid connection for server: {server_name}",
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
                        server_name,
                        prompt_name,
                    )
                    if await self._reconnect(server_name):
                        continue
                logger.error(
                    "MCP get_prompt failed (%s:%s): %s: %s",
                    server_name,
                    prompt_name,
                    type(e).__name__,
                    e,
                )
                return MCPCallResult(success=False, error=f"{type(e).__name__}: {e}")

        return MCPCallResult(success=False, error="Unexpected: retry loop exhausted")

    # ==================== Public state queries / management ====================

    def has_server(self, name: str) -> bool:
        """Check whether a server is configured."""
        return name in self._servers

    def is_connected(self, name: str) -> bool:
        """Check whether a server is currently connected."""
        return name in self._connections

    def get_server_config(self, name: str) -> MCPServerConfig | None:
        """Get a server configuration (read-only)."""
        return self._servers.get(name)

    def remove_server(self, name: str) -> None:
        """Remove the server configuration and its associated tools/resources/prompts (does not disconnect; call disconnect first)."""
        self._servers.pop(name, None)
        self._connections.pop(name, None)
        prefix = f"{name}:"
        self._tools = {k: v for k, v in self._tools.items() if not k.startswith(prefix)}
        self._resources = {k: v for k, v in self._resources.items() if not k.startswith(prefix)}
        self._prompts = {k: v for k, v in self._prompts.items() if not k.startswith(prefix)}

    async def reset(self) -> None:
        """Disconnect all servers and clear all state (used when reloading configuration)."""
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
        """List all configured servers."""
        return list(self._servers.keys())

    def list_connected(self) -> list[str]:
        """List currently connected servers."""
        return list(self._connections.keys())

    def list_tools(self, server_name: str | None = None) -> list[MCPTool]:
        """List tools."""
        if server_name:
            prefix = f"{server_name}:"
            return [t for k, t in self._tools.items() if k.startswith(prefix)]
        return list(self._tools.values())

    def list_resources(self, server_name: str | None = None) -> list[MCPResource]:
        """List resources."""
        if server_name:
            prefix = f"{server_name}:"
            return [r for k, r in self._resources.items() if k.startswith(prefix)]
        return list(self._resources.values())

    def list_prompts(self, server_name: str | None = None) -> list[MCPPrompt]:
        """List prompts."""
        if server_name:
            prefix = f"{server_name}:"
            return [p for k, p in self._prompts.items() if k.startswith(prefix)]
        return list(self._prompts.values())

    def get_tool_schemas(self) -> list[dict]:
        """Get the LLM call schema for every tool."""
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


# Global client
mcp_client = MCPClient()


# Convenience helpers
async def connect_mcp_server(name: str) -> MCPConnectResult:
    """Connect to an MCP server."""
    return await mcp_client.connect(name)


async def call_mcp_tool(server: str, tool: str, args: dict) -> MCPCallResult:
    """Call an MCP tool."""
    return await mcp_client.call_tool(server, tool, args)


def get_mcp_tool_schemas() -> list[dict]:
    """Get MCP tool schemas."""
    return mcp_client.get_tool_schemas()
