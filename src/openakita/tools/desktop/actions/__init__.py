"""
Windows 桌面自动化 - 操作模块

提供鼠标和键盘操作功能
"""

from .mouse import MouseController, get_mouse
from .keyboard import KeyboardController, get_keyboard

__all__ = [
    "MouseController",
    "KeyboardController",
    "get_mouse",
    "get_keyboard",
]
