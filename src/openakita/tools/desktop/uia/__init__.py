"""
Windows desktop automation - UIAutomation module

Implements Windows UIAutomation functionality via pywinauto.
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
