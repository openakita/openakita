"""
浏览器自动化模块

核心组件：
- BrowserManager: 浏览器生命周期管理（状态机 + 多策略启动）
- PlaywrightTools: 基于 Playwright 的直接页面操作
- BrowserUseRunner: browser-use AI Agent 集成
- chrome_finder: Chrome 检测与 Profile 管理工具函数

WebMCP 预留接口：
- discover_webmcp_tools: 在页面上发现 WebMCP 工具
- call_webmcp_tool: 调用页面上的 WebMCP 工具
"""

from .browser_use_runner import BrowserUseRunner
from .chrome_finder import detect_chrome_installation
from .manager import BrowserManager, BrowserState, StartupStrategy
from .playwright_tools import PlaywrightTools
from .webmcp import WebMCPDiscoveryResult, WebMCPTool, call_webmcp_tool, discover_webmcp_tools

__all__ = [
    "BrowserManager",
    "BrowserState",
    "StartupStrategy",
    "PlaywrightTools",
    "BrowserUseRunner",
    "detect_chrome_installation",
    "WebMCPTool",
    "WebMCPDiscoveryResult",
    "discover_webmcp_tools",
    "call_webmcp_tool",
]
