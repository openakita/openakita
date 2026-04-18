"""
Windows desktop automation - actions module

Provides mouse and keyboard action functionality
"""

from .keyboard import KeyboardController, get_keyboard
from .mouse import MouseController, get_mouse

__all__ = [
    "MouseController",
    "KeyboardController",
    "get_mouse",
    "get_keyboard",
]
