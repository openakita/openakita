"""
Chrome DevTools MCP 浏览器后端

通过 chrome-devtools-mcp（Google 官方）连接用户真实 Chrome 浏览器。
保留登录状态和密码管理器。
"""

import logging
import shutil
from typing import Any

from .base import BrowserBackend, BrowserBackendType

logger = logging.getLogger(__name__)

# Chrome DevTools MCP 的服务器标识（对应 mcps/chrome-devtools/SERVER_METADATA.json）
CHROME_DEVTOOLS_SERVER_ID = "chrome-devtools"


class ChromeDevToolsBackend(BrowserBackend):
    """
    Chrome DevTools MCP 后端

    通过 MCP 协议调用 chrome-devtools-mcp 服务器来控制 Chrome。
    需要：
    - Node.js v20.19+
    - Chrome 浏览器（推荐 144+ 以使用 autoConnect）
    - chrome-devtools-mcp 已注册为 MCP 服务器
    """

    def __init__(self, mcp_client: Any = None):
        """
        Args:
            mcp_client: MCPClient 实例
        """
        self._mcp_client = mcp_client
        self._connected = False

    @property
    def backend_type(self) -> BrowserBackendType:
        return BrowserBackendType.CHROME_DEVTOOLS_MCP

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def preserves_login_state(self) -> bool:
        return True  # 连接用户真实 Chrome

    async def is_available(self) -> bool:
        """检测 Chrome DevTools MCP 是否可用"""
        # 检查 npx 是否可用
        if not shutil.which("npx"):
            return False

        # 检查是否已注册到 MCP 客户端
        if self._mcp_client:
            servers = self._mcp_client.list_servers()
            if CHROME_DEVTOOLS_SERVER_ID in servers:
                return True

        # 即使未注册，只要 npx 可用就可以用
        return True

    async def connect(self, visible: bool = True) -> bool:
        """
        连接到 Chrome DevTools MCP

        通过 MCP 客户端连接到 chrome-devtools-mcp 服务器。
        如果服务器未连接，尝试连接。
        """
        if not self._mcp_client:
            logger.error("[ChromeDevToolsBackend] No MCP client available")
            return False

        # 检查是否已连接
        connected = self._mcp_client.list_connected()
        if CHROME_DEVTOOLS_SERVER_ID in connected:
            self._connected = True
            return True

        # 尝试连接
        success = await self._mcp_client.connect(CHROME_DEVTOOLS_SERVER_ID)
        self._connected = success
        if success:
            logger.info("[ChromeDevToolsBackend] Connected to Chrome DevTools MCP")
        else:
            logger.warning("[ChromeDevToolsBackend] Failed to connect to Chrome DevTools MCP")
        return success

    async def disconnect(self) -> None:
        """断开 Chrome DevTools MCP 连接"""
        if self._mcp_client and self._connected:
            await self._mcp_client.disconnect(CHROME_DEVTOOLS_SERVER_ID)
            self._connected = False

    async def _call(self, tool_name: str, args: dict) -> dict:
        """调用 Chrome DevTools MCP 工具"""
        if not self._mcp_client or not self._connected:
            return {"success": False, "error": "Chrome DevTools MCP not connected"}

        result = await self._mcp_client.call_tool(
            CHROME_DEVTOOLS_SERVER_ID, tool_name, args
        )
        if result.success:
            return {"success": True, "result": result.data}
        else:
            return {"success": False, "error": result.error}

    async def navigate(self, url: str) -> dict:
        return await self._call("navigate_page", {"url": url})

    async def screenshot(self, path: str | None = None, full_page: bool = False) -> dict:
        args: dict[str, Any] = {}
        if path:
            args["selector"] = None  # Chrome DevTools MCP uses selector for element screenshot
        return await self._call("take_screenshot", args)

    async def get_content(self, selector: str | None = None, format: str = "text") -> dict:
        # Chrome DevTools MCP 使用 take_snapshot 获取页面结构
        return await self._call("take_snapshot", {})

    async def click(self, selector: str | None = None, text: str | None = None) -> dict:
        target = selector or text
        if not target:
            return {"success": False, "error": "selector or text required"}
        return await self._call("click", {"selector": target})

    async def type_text(self, selector: str, text: str, clear: bool = True) -> dict:
        return await self._call("fill", {"selector": selector, "value": text})

    async def get_status(self) -> dict:
        # 尝试 list_pages 来获取状态
        result = await self._call("list_pages", {})
        if result.get("success"):
            return {
                "success": True,
                "result": {
                    "is_open": True,
                    "backend": "chrome_devtools_mcp",
                    "preserves_login": True,
                    "pages": result.get("result"),
                },
            }
        return {
            "success": True,
            "result": {
                "is_open": False,
                "backend": "chrome_devtools_mcp",
                "message": result.get("error", "Unable to get status"),
            },
        }

    async def execute_js(self, script: str) -> dict:
        return await self._call("evaluate_script", {"expression": script})

    async def list_tabs(self) -> dict:
        return await self._call("list_pages", {})
