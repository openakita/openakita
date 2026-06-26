"""
Windows 桌面自动化 - 操作模块

提供鼠标和键盘操作功能
"""

from .keyboard import KeyboardController, get_keyboard
from .mouse import MouseController, get_mouse

__all__ = [
    "MouseController",
    "KeyboardController",
    "get_mouse",
    "get_keyboard",
]
