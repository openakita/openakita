"""
OpenAkita MCP server module

Built-in MCP server implementations:
- web_search: web search based on DuckDuckGo
"""

from .web_search import mcp as web_search_mcp

__all__ = ["web_search_mcp"]
