"""
OpenAkita MCP 服务器模块

包含内置的 MCP 服务器实现：
- web_search: 基于 DuckDuckGo 的网络搜索
"""

from .web_search import mcp as web_search_mcp

__all__ = ["web_search_mcp"]

