"""StepFilter: classifies tool calls for step card visibility."""
from __future__ import annotations

from .seecrab_models import FilterResult, StepFilterConfig


class StepFilter:
    """Classifies tool calls as visible step cards or hidden internals."""

    def __init__(self, config: StepFilterConfig | None = None):
        self.config = config or StepFilterConfig()
        self._user_messages: list[str] = []

    def set_user_messages(self, messages: list[str]) -> None:
        """Set recent user messages for mention detection."""
        self._user_messages = messages[-5:]

    def classify(self, tool_name: str, args: dict) -> FilterResult:
        """Classify a tool call.

        Priority: skill_trigger > mcp_trigger > whitelist > user_mention > hidden.
        """
        if tool_name in self.config.skill_triggers:
            return FilterResult.SKILL_TRIGGER

        if tool_name == self.config.mcp_trigger:
            return FilterResult.MCP_TRIGGER

        if tool_name in self.config.whitelist:
            return FilterResult.WHITELIST

        if self._check_user_mention(tool_name):
            return FilterResult.USER_MENTION

        return FilterResult.HIDDEN

    def _check_user_mention(self, tool_name: str) -> bool:
        """Check if user recently mentioned this tool's operation."""
        keywords = self.config.user_mention_keywords.get(tool_name)
        if not keywords:
            return False
        combined = " ".join(self._user_messages).lower()
        return any(kw in combined for kw in keywords)
