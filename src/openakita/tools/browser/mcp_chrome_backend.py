"""
mcp-chrome 扩展浏览器后端

通过 mcp-chrome Chrome 扩展连接用户真实浏览器。
完全保留登录状态、Cookie 和所有浏览器扩展（包括密码管理器）。
"""

import logging
from typing import Any

from .base import BrowserBackend, BrowserBackendType

logger = logging.getLogger(__name__)

# mcp-chrome 扩展的服务器标识（对应 mcps/chrome-browser/SERVER_METADATA.json）
MCP_CHROME_SERVER_ID = "chrome-browser"
MCP_CHROME_DEFAULT_PORT = 12306


class McpChromeBackend(BrowserBackend):
    """
    mcp-chrome 扩展后端

    通过 mcp-chrome Chrome 扩展连接用户真实浏览器。
    扩展在 http://127.0.0.1:12306/mcp 暴露 MCP Streamable HTTP 接口。

    需要：
    - mcp-chrome Chrome 扩展已安装并连接
    - MCPClient 支持 streamable_http 传输协议
    """

    def __init__(self, mcp_client: Any = None, port: int = MCP_CHROME_DEFAULT_PORT):
        """
        Args:
            mcp_client: MCPClient 实例
            port: mcp-chrome 监听端口
        """
        self._mcp_client = mcp_client
        self._port = port
        self._connected = False

    @property
    def backend_type(self) -> BrowserBackendType:
        return BrowserBackendType.MCP_CHROME

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def preserves_login_state(self) -> bool:
        return True  # 完全使用用户真实浏览器

    async def is_available(self) -> bool:
        """检测 mcp-chrome 扩展是否正在运行"""
        # 直接探测端口
        from ..browser_mcp import check_mcp_chrome_extension

        return await check_mcp_chrome_extension(port=self._port)

    async def connect(self, visible: bool = True) -> bool:
        """
        连接到 mcp-chrome 扩展

        mcp-chrome 控制的是用户真实浏览器，visible 参数不适用。
        """
        if not self._mcp_client:
            logger.error("[McpChromeBackend] No MCP client available")
            return False

        # 检查是否已连接
        connected = self._mcp_client.list_connected()
        if MCP_CHROME_SERVER_ID in connected:
            self._connected = True
            return True

        # 尝试连接
        success = await self._mcp_client.connect(MCP_CHROME_SERVER_ID)
        self._connected = success
        if success:
            logger.info("[McpChromeBackend] Connected to mcp-chrome extension")
        else:
            logger.warning("[McpChromeBackend] Failed to connect to mcp-chrome extension")
        return success

    async def disconnect(self) -> None:
        """断开 mcp-chrome 连接"""
        if self._mcp_client and self._connected:
            await self._mcp_client.disconnect(MCP_CHROME_SERVER_ID)
            self._connected = False

    async def _call(self, tool_name: str, args: dict) -> dict:
        """调用 mcp-chrome 工具"""
        if not self._mcp_client or not self._connected:
            return {"success": False, "error": "mcp-chrome extension not connected"}

        result = await self._mcp_client.call_tool(
            MCP_CHROME_SERVER_ID, tool_name, args
        )
        if result.success:
            return {"success": True, "result": result.data}
        else:
            return {"success": False, "error": result.error}

    async def navigate(self, url: str) -> dict:
        return await self._call("chrome_navigate", {"url": url})

    async def screenshot(self, path: str | None = None, full_page: bool = False) -> dict:
        args: dict[str, Any] = {}
        if path:
            # mcp-chrome 可能不支持自定义路径，但传递以防万一
            args["path"] = path
        return await self._call("chrome_screenshot", args)

    async def get_content(self, selector: str | None = None, format: str = "text") -> dict:
        args: dict[str, Any] = {}
        if selector:
            args["selector"] = selector
        return await self._call("chrome_get_content", args)

    async def click(self, selector: str | None = None, text: str | None = None) -> dict:
        target = selector or text
        if not target:
            return {"success": False, "error": "selector or text required"}
        return await self._call("chrome_click", {"selector": target})

    async def type_text(self, selector: str, text: str, clear: bool = True) -> dict:
        return await self._call("chrome_type", {"selector": selector, "text": text})

    async def get_status(self) -> dict:
        # mcp-chrome 没有专门的 status 命令，尝试一个轻量操作
        try:
            result = await self._call("chrome_get_content", {})
            is_open = result.get("success", False)
        except Exception:
            is_open = False

        return {
            "success": True,
            "result": {
                "is_open": is_open,
                "backend": "mcp_chrome",
                "preserves_login": True,
                "message": "Connected to user's Chrome via mcp-chrome extension" if is_open else "mcp-chrome extension not responding",
            },
        }

    async def execute_js(self, script: str) -> dict:
        # mcp-chrome 可能没有直接的 JS 执行接口
        return {"success": False, "error": "JavaScript execution not supported via mcp-chrome"}
