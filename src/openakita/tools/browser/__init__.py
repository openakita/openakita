"""
Browser automation module

Core components:
- BrowserManager: browser lifecycle management (state machine + multi-strategy launch)
- PlaywrightTools: Playwright-based direct page operations
- chrome_finder: Chrome detection and profile management utilities

WebMCP reserved interface:
- discover_webmcp_tools: discover WebMCP tools on a page
- call_webmcp_tool: invoke a WebMCP tool on a page
"""

from .chrome_finder import detect_chrome_installation
from .manager import BrowserManager, BrowserState, StartupStrategy
from .playwright_tools import PlaywrightTools
from .webmcp import WebMCPDiscoveryResult, WebMCPTool, call_webmcp_tool, discover_webmcp_tools

__all__ = [
    "BrowserManager",
    "BrowserState",
    "StartupStrategy",
    "PlaywrightTools",
    "detect_chrome_installation",
    "WebMCPTool",
    "WebMCPDiscoveryResult",
    "discover_webmcp_tools",
    "call_webmcp_tool",
]
