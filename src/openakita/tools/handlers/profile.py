"""
User profile handler

Handles system skills related to user profiles:
- update_user_profile: update profile
- skip_profile_question: skip question
- get_user_profile: get profile
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class ProfileHandler:
    """User profile handler"""

    TOOLS = [
        "update_user_profile",
        "skip_profile_question",
        "get_user_profile",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle tool invocation"""
        if tool_name == "update_user_profile":
            return self._update_profile(params)
        elif tool_name == "skip_profile_question":
            return self._skip_question(params)
        elif tool_name == "get_user_profile":
            return self._get_profile(params)
        else:
            return f"❌ Unknown profile tool: {tool_name}"

    def _update_profile(self, params: dict) -> str:
        """Update user profile"""
        available_keys = self.agent.profile_manager.get_available_keys()

        # LLM may pass {name: "Xiao Ming", age: "28"} instead of {key: "name", value: "Xiao Ming"}
        if "key" not in params:
            updated = []
            for k, v in params.items():
                if k in available_keys:
                    self.agent.profile_manager.update_profile(k, str(v))
                    updated.append(f"{k} = {v}")
            if updated:
                return f"✅ Profile updated: {', '.join(updated)}"
            return (
                f"❌ Invalid parameter format. Correct usage: {{\"key\": \"name\", \"value\": \"value\"}}\n"
                f"Available keys: {', '.join(available_keys)}"
            )

        key = params["key"]
        value = params.get("value", "")

        if key not in available_keys:
            return f"❌ Unknown profile key: {key}\nAvailable keys: {', '.join(available_keys)}"

        self.agent.profile_manager.update_profile(key, value)
        return f"✅ Profile updated: {key} = {value}"

    def _skip_question(self, params: dict) -> str:
        """Skip profile question"""
        key = params["key"]
        self.agent.profile_manager.skip_question(key)
        return f"✅ Question skipped: {key}"

    def _get_profile(self, params: dict) -> str:
        """Get user profile"""
        summary = self.agent.profile_manager.get_profile_summary()

        if not summary:
            return "User profile is empty\n\nTip: Share information in conversation to build your profile"

        return summary


def create_handler(agent: "Agent"):
    """Create user profile handler"""
    handler = ProfileHandler(agent)
    return handler.handle
