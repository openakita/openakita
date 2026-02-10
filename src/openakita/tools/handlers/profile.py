"""
用户档案处理器

处理用户档案相关的系统技能：
- update_user_profile: 更新档案
- skip_profile_question: 跳过问题
- get_user_profile: 获取档案
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class ProfileHandler:
    """用户档案处理器"""

    TOOLS = [
        "update_user_profile",
        "skip_profile_question",
        "get_user_profile",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if tool_name == "update_user_profile":
            return self._update_profile(params)
        elif tool_name == "skip_profile_question":
            return self._skip_question(params)
        elif tool_name == "get_user_profile":
            return self._get_profile(params)
        else:
            return f"❌ Unknown profile tool: {tool_name}"

    def _update_profile(self, params: dict) -> str:
        """更新用户档案"""
        key = params["key"]
        value = params["value"]

        available_keys = self.agent.profile_manager.get_available_keys()
        if key not in available_keys:
            return f"❌ 未知的档案项: {key}\n可用的键: {', '.join(available_keys)}"

        self.agent.profile_manager.update_profile(key, value)
        return f"✅ 已更新档案: {key} = {value}"

    def _skip_question(self, params: dict) -> str:
        """跳过档案问题"""
        key = params["key"]
        self.agent.profile_manager.skip_question(key)
        return f"✅ 已跳过问题: {key}"

    def _get_profile(self, params: dict) -> str:
        """获取用户档案"""
        summary = self.agent.profile_manager.get_profile_summary()

        if not summary:
            return "用户档案为空\n\n提示: 通过对话中分享信息来建立档案"

        return summary


def create_handler(agent: "Agent"):
    """创建用户档案处理器"""
    handler = ProfileHandler(agent)
    return handler.handle
