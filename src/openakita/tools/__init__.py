"""
OpenAkita 工具模块
"""

import sys

from .file import FileTool
from .mcp import MCPClient, MCPConnectResult, mcp_client
from .mcp_catalog import MCPCatalog, mcp_catalog, scan_mcp_servers
from .shell import ShellTool
from .web import WebTool

__all__ = [
    "ShellTool",
    "FileTool",
    "WebTool",
    "MCPClient",
    "MCPConnectResult",
    "mcp_client",
    "MCPCatalog",
    "mcp_catalog",
    "scan_mcp_servers",
]

# Windows 桌面自动化模块（仅 Windows 平台可用）
# 延迟导入：pyautogui 在某些 Windows 环境下初始化极慢，
# 改为按需导入（首次使用桌面工具时才加载）。
_DESKTOP_LOADED = False


def _ensure_desktop_loaded():
    """按需加载桌面自动化模块，避免模块级导入阻塞整个包。"""
    global _DESKTOP_LOADED
    if _DESKTOP_LOADED:
        return True
    if sys.platform != "win32":
        return False
    try:
        from .desktop import (  # noqa: F401
            DESKTOP_TOOLS,
            DesktopController,
            DesktopToolHandler,
            KeyboardController,
            MouseController,
            ScreenCapture,
            UIAClient,
            VisionAnalyzer,
            get_controller,
            register_desktop_tools,
        )

        _g = globals()
        _g["DESKTOP_TOOLS"] = DESKTOP_TOOLS
        _g["DesktopController"] = DesktopController
        _g["DesktopToolHandler"] = DesktopToolHandler
        _g["KeyboardController"] = KeyboardController
        _g["MouseController"] = MouseController
        _g["ScreenCapture"] = ScreenCapture
        _g["UIAClient"] = UIAClient
        _g["VisionAnalyzer"] = VisionAnalyzer
        _g["get_controller"] = get_controller
        _g["register_desktop_tools"] = register_desktop_tools

        __all__.extend(
            [
                "DesktopController",
                "get_controller",
                "ScreenCapture",
                "MouseController",
                "KeyboardController",
                "UIAClient",
                "VisionAnalyzer",
                "DESKTOP_TOOLS",
                "DesktopToolHandler",
                "register_desktop_tools",
            ]
        )
        _DESKTOP_LOADED = True
        return True
    except ImportError as e:
        import logging

        logging.getLogger(__name__).debug(
            f"Desktop automation module not available: {e}. "
            "Install with: pip install mss pyautogui pywinauto"
        )
        return False

