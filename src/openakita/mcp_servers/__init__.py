"""
OpenAkita MCP 服务器模块

包含内置的 MCP 服务器实现：
- web_search: 基于 DuckDuckGo 的网络搜索
- desktop_control: 基于视觉的桌面自动化（截屏 + pyautogui）
"""

from .desktop_control import mcp as desktop_control_mcp
from .web_search import mcp as web_search_mcp

__all__ = ["web_search_mcp", "desktop_control_mcp"]
