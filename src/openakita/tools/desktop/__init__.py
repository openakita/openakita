"""
OpenAkita - Windows Desktop Automation Module

Provides Windows desktop automation capabilities:
- UIAutomation (pywinauto) - Fast element operations for standard Windows apps
- Visual Recognition (DashScope Qwen-VL) - Smart recognition for non-standard UIs
- Screenshot (mss) - High-performance screen capture
- Mouse/Keyboard (PyAutoGUI) - Input control

Important:
This module is for controlling Windows desktop applications.
If the task only involves in-browser web operations (e.g., opening URLs, clicking web buttons,
filling forms), prefer the browser_* tools (based on Playwright) over desktop automation tools.

Desktop automation tools are suitable for:
- Operating non-browser desktop apps (e.g., Notepad, Office, File Explorer)
- Controlling the browser window itself (e.g., switching tabs, resizing windows)
- Mixed desktop and browser interaction scenarios
"""

import sys

# Platform check
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

# Core types
# Actions
from .actions import KeyboardController, MouseController, get_keyboard, get_mouse

# Cache
from .cache import ElementCache, clear_cache, get_cache

# Screenshot
from .capture import ScreenCapture, get_capture, screenshot, screenshot_base64

# Configuration
from .config import (
    ActionConfig,
    CaptureConfig,
    DesktopConfig,
    UIAConfig,
    VisionConfig,
    get_config,
    set_config,
)

# Main controller
from .controller import DesktopController, get_controller

# Agent tools
from .tools import (
    DESKTOP_TOOLS,
    DesktopToolHandler,
    register_desktop_tools,
)
from .types import (
    ActionResult,
    BoundingBox,
    ControlType,
    ElementLocation,
    FindMethod,
    MouseButton,
    ScrollDirection,
    UIElement,
    VisionResult,
    WindowAction,
    WindowInfo,
)

# UI Automation
from .uia import UIAClient, UIAElement, UIAElementWrapper, UIAInspector, get_uia_client

# Visual recognition
from .vision import PromptTemplates, VisionAnalyzer, get_vision_analyzer

__all__ = [
    # Types
    "UIElement",
    "WindowInfo",
    "BoundingBox",
    "ActionResult",
    "ElementLocation",
    "VisionResult",
    "ControlType",
    "MouseButton",
    "ScrollDirection",
    "FindMethod",
    "WindowAction",
    # Configuration
    "DesktopConfig",
    "CaptureConfig",
    "UIAConfig",
    "VisionConfig",
    "ActionConfig",
    "get_config",
    "set_config",
    # Main controller
    "DesktopController",
    "get_controller",
    # Screenshot
    "ScreenCapture",
    "get_capture",
    "screenshot",
    "screenshot_base64",
    # Actions
    "MouseController",
    "KeyboardController",
    "get_mouse",
    "get_keyboard",
    # UI Automation
    "UIAClient",
    "UIAElement",
    "UIAElementWrapper",
    "UIAInspector",
    "get_uia_client",
    # Vision
    "VisionAnalyzer",
    "PromptTemplates",
    "get_vision_analyzer",
    # Cache
    "ElementCache",
    "get_cache",
    "clear_cache",
    # Tools
    "DESKTOP_TOOLS",
    "DesktopToolHandler",
    "register_desktop_tools",
]
