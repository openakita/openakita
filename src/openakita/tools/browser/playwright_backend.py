"""
Playwright 浏览器后端

封装现有 BrowserMCP 中的 Playwright 逻辑为统一的 BrowserBackend 接口。
这是默认后端，不保留用户登录状态。
"""

import logging
from typing import Any

from .base import BrowserBackend, BrowserBackendType

logger = logging.getLogger(__name__)


class PlaywrightBackend(BrowserBackend):
    """
    基于 Playwright 的浏览器后端

    这是默认的浏览器后端，使用 Playwright 控制 Chromium。
    不保留用户登录状态（启动全新浏览器实例），但稳定可靠。

    注意：此后端是对现有 BrowserMCP 的薄封装。BrowserMCP 本身已有完整的
    Playwright 实现，此类的主要作用是适配 BrowserBackend ABC 接口，
    为后续多后端自动切换做准备。
    """

    def __init__(self, browser_mcp: Any = None):
        """
        Args:
            browser_mcp: 现有的 BrowserMCP 实例（可选，延迟注入）
        """
        self._browser_mcp = browser_mcp

    @property
    def backend_type(self) -> BrowserBackendType:
        return BrowserBackendType.PLAYWRIGHT

    @property
    def is_connected(self) -> bool:
        if self._browser_mcp is None:
            return False
        return getattr(self._browser_mcp, "_started", False)

    @property
    def preserves_login_state(self) -> bool:
        # Playwright 默认不保留登录态（除非 use_user_chrome 且连接成功）
        if self._browser_mcp:
            return getattr(self._browser_mcp, "_using_user_chrome", False)
        return False

    def set_browser_mcp(self, browser_mcp: Any) -> None:
        """注入 BrowserMCP 实例"""
        self._browser_mcp = browser_mcp

    async def is_available(self) -> bool:
        """检测 Playwright 是否可用"""
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    async def connect(self, visible: bool = True) -> bool:
        """启动浏览器"""
        if self._browser_mcp is None:
            logger.error("[PlaywrightBackend] No BrowserMCP instance available")
            return False
        return await self._browser_mcp.start(visible=visible)

    async def disconnect(self) -> None:
        """关闭浏览器"""
        if self._browser_mcp:
            await self._browser_mcp.stop()

    async def navigate(self, url: str) -> dict:
        """导航到 URL"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        return await self._browser_mcp.call_tool("browser_navigate", {"url": url})

    async def screenshot(self, path: str | None = None, full_page: bool = False) -> dict:
        """截取截图"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        args: dict[str, Any] = {"full_page": full_page}
        if path:
            args["path"] = path
        return await self._browser_mcp.call_tool("browser_screenshot", args)

    async def get_content(self, selector: str | None = None, format: str = "text") -> dict:
        """获取页面内容"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        args: dict[str, Any] = {"format": format}
        if selector:
            args["selector"] = selector
        return await self._browser_mcp.call_tool("browser_get_content", args)

    async def click(self, selector: str | None = None, text: str | None = None) -> dict:
        """点击元素"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        args: dict[str, Any] = {}
        if selector:
            args["selector"] = selector
        if text:
            args["text"] = text
        return await self._browser_mcp.call_tool("browser_click", args)

    async def type_text(self, selector: str, text: str, clear: bool = True) -> dict:
        """输入文本"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        return await self._browser_mcp.call_tool(
            "browser_type", {"selector": selector, "text": text, "clear": clear}
        )

    async def get_status(self) -> dict:
        """获取浏览器状态"""
        if not self._browser_mcp:
            return {
                "success": True,
                "result": {"is_open": False, "message": "Playwright backend not initialized"},
            }
        return await self._browser_mcp.call_tool("browser_status", {})

    async def execute_js(self, script: str) -> dict:
        """执行 JavaScript"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        return await self._browser_mcp.call_tool("browser_execute_js", {"script": script})

    async def wait(self, selector: str | None = None, timeout: int = 30000) -> dict:
        """等待"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        args: dict[str, Any] = {"timeout": timeout}
        if selector:
            args["selector"] = selector
        return await self._browser_mcp.call_tool("browser_wait", args)

    async def scroll(self, direction: str = "down", amount: int = 500) -> dict:
        """滚动"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        return await self._browser_mcp.call_tool(
            "browser_scroll", {"direction": direction, "amount": amount}
        )

    async def list_tabs(self) -> dict:
        """列出标签页"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        return await self._browser_mcp.call_tool("browser_list_tabs", {})

    async def switch_tab(self, index: int) -> dict:
        """切换标签页"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        return await self._browser_mcp.call_tool("browser_switch_tab", {"index": index})

    async def new_tab(self, url: str) -> dict:
        """新建标签页"""
        if not self._browser_mcp:
            return {"success": False, "error": "Playwright backend not initialized"}
        return await self._browser_mcp.call_tool("browser_new_tab", {"url": url})
