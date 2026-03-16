"""CardBuilder: assembles step_card SSE events."""
from __future__ import annotations

from fnmatch import fnmatch


class CardBuilder:
    """Assembles step_card event dicts with card_type inference."""

    CARD_TYPE_MAP: dict[str, str] = {
        "web_search": "search",
        "news_search": "search",
        "search_*": "search",
        "code_execute": "code",
        "python_execute": "code",
        "shell_execute": "code",
        "generate_report": "file",

        "export_*": "file",
        "analyze_data": "analysis",
        "chart_*": "analysis",
        "browser_*": "browser",
        "navigate_*": "browser",
    }

    def build_step_card(
        self,
        step_id: str,
        title: str,
        status: str,
        source_type: str,
        tool_name: str,
        plan_step_index: int | None = None,
        agent_id: str = "main",
        duration: float | None = None,
        input_data: dict | None = None,
        output_data: str | None = None,
        absorbed_calls: list[dict] | None = None,
    ) -> dict:
        """Assemble a complete step_card event."""
        return {
            "type": "step_card",
            "step_id": step_id,
            "title": title,
            "status": status,
            "source_type": source_type,
            "card_type": self._get_card_type(tool_name),
            "duration": duration,
            "plan_step_index": plan_step_index,
            "agent_id": agent_id,
            "input": input_data,
            "output": output_data,
            "absorbed_calls": absorbed_calls or [],
        }

    def _get_card_type(self, tool_name: str) -> str:
        """Infer card_type from tool_name using exact + wildcard matching."""
        if tool_name in self.CARD_TYPE_MAP:
            return self.CARD_TYPE_MAP[tool_name]
        for pattern, card_type in self.CARD_TYPE_MAP.items():
            if "*" in pattern and fnmatch(tool_name, pattern):
                return card_type
        return "default"
