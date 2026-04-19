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
        """Update user profile.

        - Known keys are persisted directly to the profile.
        - Unknown keys automatically fall back to add_memory, stored as a
          fact in semantic memory, so non-technical users don't hit a dead
          end when the whitelist doesn't cover their input.
        """
        available_keys = self.agent.profile_manager.get_available_keys()

        # LLM may pass {name: "Xiao Ming", age: "28"} instead of {key: "name", value: "Xiao Ming"}
        if "key" not in params:
            updated: list[str] = []
            saved_as_memory: list[str] = []
            for k, v in params.items():
                if k in available_keys:
                    self.agent.profile_manager.update_profile(k, str(v))
                    updated.append(f"{k} = {v}")
                else:
                    if self._save_unknown_as_memory(k, v):
                        saved_as_memory.append(f"{k} = {v}")
            parts: list[str] = []
            if updated:
                parts.append(f"✅ Profile updated: {', '.join(updated)}")
            if saved_as_memory:
                parts.append(
                    f"📝 The following were not in the profile whitelist and were saved as long-term memory: "
                    f"{', '.join(saved_as_memory)}"
                )
            if parts:
                return "\n".join(parts)
            return (
                f"❌ Invalid parameter format. Correct usage: {{\"key\": \"name\", \"value\": \"value\"}}\n"
                f"Available keys: {', '.join(available_keys)}"
            )

        key = params["key"]
        value = params.get("value", "")

        if key not in available_keys:
            if self._save_unknown_as_memory(key, value):
                return (
                    f"📝 The profile whitelist does not include `{key}`; saved as long-term memory: {key} = {value}\n"
                    f"(To formalize this field, ask an admin to extend USER_PROFILE_ITEMS.)"
                )
            return f"❌ Unknown profile key: {key}\nAvailable keys: {', '.join(available_keys)}"

        self.agent.profile_manager.update_profile(key, value)
        return f"✅ Profile updated: {key} = {value}"

    def _save_unknown_as_memory(self, key: str, value: Any) -> bool:
        """Save an out-of-whitelist key=value into semantic memory as a fact.

        Returns False on failure; the caller decides whether to surface an error.
        """
        try:
            mm = getattr(self.agent, "memory_manager", None)
            if mm is None or not hasattr(mm, "add_memory"):
                return False
            from ...memory.types import Memory, MemoryPriority, MemoryType

            content = f"User profile supplement: {key} = {value}"
            mem = Memory(
                content=content,
                type=MemoryType.FACT,
                priority=MemoryPriority.LONG_TERM,
                source="profile_fallback",
                importance_score=0.7,
                tags=["profile_extra", key],
            )
            mm.add_memory(mem)
            return True
        except Exception as e:
            logger.warning(f"[ProfileHandler] fallback to memory failed: {e}")
            return False

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
