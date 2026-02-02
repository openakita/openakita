"""
OpenAkita - Windows 桌面自动化模块

提供 Windows 桌面的自动化控制能力：
- UIAutomation (pywinauto) - 标准 Windows 应用的快速元素操作
- 视觉识别 (DashScope Qwen-VL) - 非标准 UI 的智能识别
- 截图 (mss) - 高性能屏幕截取
- 鼠标/键盘 (PyAutoGUI) - 输入控制

重要说明：
此模块用于控制 Windows 桌面应用程序。
如果任务只涉及浏览器内的网页操作（如打开网址、点击网页按钮、填写表单等），
应优先使用 browser_* 系列工具（基于 Playwright），而不是桌面自动化工具。

桌面自动化工具适用于：
- 操作非浏览器的桌面应用（如记事本、Office、文件管理器）
- 需要操作浏览器窗口本身（如切换标签页、调整窗口大小）
- 与桌面和浏览器混合操作的场景
"""

import sys

# 平台检查
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. "
        f"Current platform: {sys.platform}"
    )

# 核心类型
from .types import (
    UIElement,
    WindowInfo,
    BoundingBox,
    ActionResult,
    ElementLocation,
    VisionResult,
    ControlType,
    MouseButton,
    ScrollDirection,
    FindMethod,
    WindowAction,
)

# 配置
from .config import (
    DesktopConfig,
    CaptureConfig,
    UIAConfig,
    VisionConfig,
    ActionConfig,
    get_config,
    set_config,
)

# 主控制器
from .controller import DesktopController, get_controller

# 截图
from .capture import ScreenCapture, get_capture, screenshot, screenshot_base64

# 操作
from .actions import MouseController, KeyboardController, get_mouse, get_keyboard

# UIAutomation
from .uia import UIAClient, UIAElement, UIAElementWrapper, UIAInspector, get_uia_client

# 视觉识别
from .vision import VisionAnalyzer, PromptTemplates, get_vision_analyzer

# 缓存
from .cache import ElementCache, get_cache, clear_cache

# Agent 工具
from .tools import (
    DESKTOP_TOOLS,
    DesktopToolHandler,
    register_desktop_tools,
)

__all__ = [
    # 类型
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
    # 配置
    "DesktopConfig",
    "CaptureConfig",
    "UIAConfig",
    "VisionConfig",
    "ActionConfig",
    "get_config",
    "set_config",
    # 主控制器
    "DesktopController",
    "get_controller",
    # 截图
    "ScreenCapture",
    "get_capture",
    "screenshot",
    "screenshot_base64",
    # 操作
    "MouseController",
    "KeyboardController",
    "get_mouse",
    "get_keyboard",
    # UIAutomation
    "UIAClient",
    "UIAElement",
    "UIAElementWrapper",
    "UIAInspector",
    "get_uia_client",
    # 视觉
    "VisionAnalyzer",
    "PromptTemplates",
    "get_vision_analyzer",
    # 缓存
    "ElementCache",
    "get_cache",
    "clear_cache",
    # 工具
    "DESKTOP_TOOLS",
    "DesktopToolHandler",
    "register_desktop_tools",
]
