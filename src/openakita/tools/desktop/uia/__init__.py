"""
Windows 桌面自动化 - UIAutomation 模块

基于 pywinauto 实现 Windows UIAutomation 功能
"""

from .client import UIAClient, get_uia_client
from .elements import UIAElement, UIAElementWrapper
from .inspector import UIAInspector

__all__ = [
    "UIAClient",
    "UIAElement",
    "UIAElementWrapper",
    "UIAInspector",
    "get_uia_client",
]
