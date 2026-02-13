"""
浏览器后端抽象基类

定义统一的浏览器操作接口，所有后端实现都应遵循此 ABC。
"""

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class BrowserBackendType(Enum):
    """浏览器后端类型"""

    PLAYWRIGHT = "playwright"
    CHROME_DEVTOOLS_MCP = "chrome_devtools_mcp"
    MCP_CHROME = "mcp_chrome"


class BrowserBackend(ABC):
    """
    浏览器后端抽象基类

    所有浏览器后端都应实现此接口，以便上层工具（如 BrowserMCP）
    可以透明地切换不同的浏览器控制方式。
    """

    @property
    @abstractmethod
    def backend_type(self) -> BrowserBackendType:
        """返回后端类型"""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """是否已连接到浏览器"""
        ...

    @property
    @abstractmethod
    def preserves_login_state(self) -> bool:
        """此后端是否保留用户登录状态"""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """
        检测此后端是否可用（环境检测）

        Returns:
            是否可用
        """
        ...

    @abstractmethod
    async def connect(self, visible: bool = True) -> bool:
        """
        连接/启动浏览器

        Args:
            visible: 是否显示浏览器窗口

        Returns:
            是否成功
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开/关闭浏览器"""
        ...

    @abstractmethod
    async def navigate(self, url: str) -> dict:
        """
        导航到指定 URL

        Args:
            url: 目标 URL

        Returns:
            {"success": bool, "result": str | None, "error": str | None}
        """
        ...

    @abstractmethod
    async def screenshot(self, path: str | None = None, full_page: bool = False) -> dict:
        """
        截取页面截图

        Args:
            path: 保存路径（可选）
            full_page: 是否截取整页

        Returns:
            {"success": bool, "result": str (文件路径), "error": str | None}
        """
        ...

    @abstractmethod
    async def get_content(self, selector: str | None = None, format: str = "text") -> dict:
        """
        获取页面内容

        Args:
            selector: CSS 选择器（可选，默认获取整页）
            format: 输出格式，"text" 或 "html"

        Returns:
            {"success": bool, "result": str, "error": str | None}
        """
        ...

    @abstractmethod
    async def click(self, selector: str | None = None, text: str | None = None) -> dict:
        """
        点击页面元素

        Args:
            selector: CSS 选择器
            text: 元素文本（模糊匹配）

        Returns:
            {"success": bool, "result": str | None, "error": str | None}
        """
        ...

    @abstractmethod
    async def type_text(
        self, selector: str, text: str, clear: bool = True
    ) -> dict:
        """
        在输入框中输入文本

        Args:
            selector: 输入框选择器
            text: 要输入的文本
            clear: 是否先清空

        Returns:
            {"success": bool, "result": str | None, "error": str | None}
        """
        ...

    @abstractmethod
    async def get_status(self) -> dict:
        """
        获取浏览器状态

        Returns:
            {"success": bool, "result": {"is_open": bool, "url": str, "title": str, "tab_count": int, ...}}
        """
        ...

    @abstractmethod
    async def execute_js(self, script: str) -> dict:
        """
        执行 JavaScript

        Args:
            script: JavaScript 代码

        Returns:
            {"success": bool, "result": Any, "error": str | None}
        """
        ...

    # --- 可选方法（有默认实现） ---

    async def wait(self, selector: str | None = None, timeout: int = 30000) -> dict:
        """等待元素或时间"""
        return {"success": False, "error": "Not implemented by this backend"}

    async def scroll(self, direction: str = "down", amount: int = 500) -> dict:
        """滚动页面"""
        return {"success": False, "error": "Not implemented by this backend"}

    async def list_tabs(self) -> dict:
        """列出所有标签页"""
        return {"success": False, "error": "Not implemented by this backend"}

    async def switch_tab(self, index: int) -> dict:
        """切换标签页"""
        return {"success": False, "error": "Not implemented by this backend"}

    async def new_tab(self, url: str) -> dict:
        """新建标签页"""
        return {"success": False, "error": "Not implemented by this backend"}

    async def close(self) -> dict:
        """关闭浏览器"""
        await self.disconnect()
        return {"success": True, "result": "Browser closed"}


async def auto_select_backend(
    mcp_client: Any = None,
) -> BrowserBackend | None:
    """
    自动选择最佳浏览器后端

    优先级：
    1. mcp-chrome 扩展（完全保留登录态+扩展）
    2. Chrome DevTools MCP（保留登录态）
    3. Playwright（默认，无登录态）

    Args:
        mcp_client: MCP 客户端实例，用于检测外部 MCP 后端

    Returns:
        最佳可用后端实例，或 None
    """
    # 延迟导入，避免循环依赖
    from .mcp_chrome_backend import McpChromeBackend
    from .chrome_devtools_backend import ChromeDevToolsBackend
    from .playwright_backend import PlaywrightBackend

    # 1. 尝试 mcp-chrome 扩展
    try:
        mcp_chrome = McpChromeBackend(mcp_client=mcp_client)
        if await mcp_chrome.is_available():
            logger.info("[AutoSelect] mcp-chrome extension detected, using McpChromeBackend")
            return mcp_chrome
    except Exception as e:
        logger.debug(f"[AutoSelect] mcp-chrome check failed: {e}")

    # 2. 尝试 Chrome DevTools MCP
    try:
        devtools = ChromeDevToolsBackend(mcp_client=mcp_client)
        if await devtools.is_available():
            logger.info("[AutoSelect] Chrome DevTools MCP detected, using ChromeDevToolsBackend")
            return devtools
    except Exception as e:
        logger.debug(f"[AutoSelect] Chrome DevTools MCP check failed: {e}")

    # 3. 回退到 Playwright
    try:
        pw = PlaywrightBackend()
        if await pw.is_available():
            logger.info("[AutoSelect] Using PlaywrightBackend (default)")
            return pw
    except Exception as e:
        logger.debug(f"[AutoSelect] Playwright check failed: {e}")

    logger.error("[AutoSelect] No browser backend available!")
    return None
