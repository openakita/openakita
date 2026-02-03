"""
浏览器处理器

处理浏览器相关的系统技能：
- browser_open, browser_status, browser_navigate, browser_click, 
- browser_type, browser_get_content, browser_screenshot,
- browser_list_tabs, browser_switch_tab, browser_new_tab
"""

import logging
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class BrowserHandler:
    """
    浏览器处理器
    
    通过 browser_mcp 处理所有浏览器相关的工具调用
    """
    
    TOOLS = [
        "browser_task",  # 【推荐优先使用】智能浏览器任务，放在最前面以表示优先级
        "browser_open",
        "browser_status",
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_get_content",
        "browser_screenshot",
        "browser_list_tabs",
        "browser_switch_tab",
        "browser_new_tab",
    ]
    
    def __init__(self, agent: "Agent"):
        self.agent = agent
    
    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if not hasattr(self.agent, 'browser_mcp') or not self.agent.browser_mcp:
            return "❌ 浏览器 MCP 未启动。请确保已安装 playwright: pip install playwright && playwright install chromium"
        
        # 提取实际工具名（处理 mcp__browser-use__browser_navigate 格式）
        actual_tool_name = tool_name
        if "browser_" in tool_name and not tool_name.startswith("browser_"):
            match = re.search(r'(browser_\w+)', tool_name)
            if match:
                actual_tool_name = match.group(1)
        
        result = await self.agent.browser_mcp.call_tool(actual_tool_name, params)
        
        if result.get("success"):
            return f"✅ {result.get('result', 'OK')}"
        else:
            return f"❌ {result.get('error', '未知错误')}"


def create_handler(agent: "Agent"):
    """创建浏览器处理器"""
    handler = BrowserHandler(agent)
    return handler.handle
