"""
浏览器后端抽象层

提供统一的浏览器操作接口，支持多种后端实现：
- PlaywrightBackend: 基于 Playwright 的内置浏览器（默认）
- ChromeDevToolsBackend: 通过 Chrome DevTools MCP 连接用户 Chrome
- McpChromeBackend: 通过 mcp-chrome 扩展连接用户 Chrome

WebMCP 预留接口：
- discover_webmcp_tools: 在页面上发现 WebMCP 工具
- call_webmcp_tool: 调用页面上的 WebMCP 工具
"""

from .base import BrowserBackend, BrowserBackendType, auto_select_backend
from .webmcp import WebMCPTool, WebMCPDiscoveryResult, discover_webmcp_tools, call_webmcp_tool

__all__ = [
    "BrowserBackend",
    "BrowserBackendType",
    "auto_select_backend",
    "WebMCPTool",
    "WebMCPDiscoveryResult",
    "discover_webmcp_tools",
    "call_webmcp_tool",
]
