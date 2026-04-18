"""
OpenAkita tools module
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

# Windows desktop automation module (only available on Windows)
# Lazy import: pyautogui initializes very slowly on some Windows environments,
# so we import on demand (loaded only when desktop tools are first used).
_DESKTOP_LOADED = False


def _ensure_desktop_loaded():
    """Lazily load the desktop automation module to avoid blocking the entire package at module-level import."""
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
