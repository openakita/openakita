"""
Tool Factory

Modeled after Claude Code's buildTool + ToolDef pattern:
- Declarative tool definitions
- Automatic default-value filling
- Unified registration entry point
- Concurrency-safe / read-only / destructive flags
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TOOL_DEFAULTS = {
    "is_enabled": True,
    "is_concurrency_safe": False,
    "is_read_only": False,
    "is_destructive": False,
    "interrupt_behavior": "block",  # 'cancel' or 'block'
    "category": "general",
}


@dataclass
class ToolDef:
    """Declarative tool definition."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Coroutine]

    # Behavior flags — can be static bool or callable(input) -> bool
    is_concurrency_safe: bool | Callable[[dict], bool] = False
    is_read_only: bool | Callable[[dict], bool] = False
    is_destructive: bool | Callable[[dict], bool] = False

    # Configuration
    interrupt_behavior: str = "block"  # 'cancel' | 'block'
    category: str = "general"
    search_hint: str = ""
    is_enabled: bool | Callable[[], bool] = True

    # Context modifier
    context_modifier: Callable | None = None

    def check_concurrency_safe(self, tool_input: dict) -> bool:
        """Check whether the tool is concurrency-safe for the given input."""
        if callable(self.is_concurrency_safe):
            return self.is_concurrency_safe(tool_input)
        return bool(self.is_concurrency_safe)

    def check_read_only(self, tool_input: dict) -> bool:
        """Check whether the tool is read-only for the given input."""
        if callable(self.is_read_only):
            return self.is_read_only(tool_input)
        return bool(self.is_read_only)

    def check_destructive(self, tool_input: dict) -> bool:
        """Check whether the tool is destructive for the given input."""
        if callable(self.is_destructive):
            return self.is_destructive(tool_input)
        return bool(self.is_destructive)

    def check_enabled(self) -> bool:
        """Check whether the tool is enabled."""
        if callable(self.is_enabled):
            return self.is_enabled()
        return bool(self.is_enabled)


def build_tool(tool_def: ToolDef) -> dict:
    """Generate complete tool registration info from a ToolDef.

    Returns a dict compatible with the existing SystemHandlerRegistry.
    """
    return {
        "name": tool_def.name,
        "description": tool_def.description,
        "input_schema": tool_def.input_schema,
        "handler": tool_def.handler,
        "is_concurrency_safe": tool_def.is_concurrency_safe,
        "is_read_only": tool_def.is_read_only,
        "is_destructive": tool_def.is_destructive,
        "interrupt_behavior": tool_def.interrupt_behavior,
        "category": tool_def.category,
        "search_hint": tool_def.search_hint,
        "context_modifier": tool_def.context_modifier,
    }


def build_tool_schema(tool_def: ToolDef) -> dict:
    """Generate an LLM function-calling schema."""
    return {
        "name": tool_def.name,
        "description": tool_def.description,
        "input_schema": tool_def.input_schema,
    }


class ToolRegistry:
    """Tool registry based on ToolDef.

    Coexists with the existing SystemHandlerRegistry during gradual migration.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef) -> None:
        """Register a tool."""
        self._tools[tool_def.name] = tool_def
        logger.debug("Registered tool: %s (category=%s)", tool_def.name, tool_def.category)

    def get(self, name: str) -> ToolDef | None:
        """Get a tool definition by name."""
        return self._tools.get(name)

    def get_enabled_tools(self) -> list[ToolDef]:
        """Return all enabled tools."""
        return [t for t in self._tools.values() if t.check_enabled()]

    def get_schemas(self, *, sorted_for_cache: bool = True) -> list[dict]:
        """Return LLM schemas for all enabled tools.

        Args:
            sorted_for_cache: sort by name to ensure prompt cache stability
        """
        tools = self.get_enabled_tools()
        if sorted_for_cache:
            tools = sorted(tools, key=lambda t: t.name)
        return [build_tool_schema(t) for t in tools]

    def is_concurrency_safe(self, name: str, tool_input: dict) -> bool:
        """Query whether the tool is concurrency-safe for the given input."""
        tool = self._tools.get(name)
        if not tool:
            return False
        return tool.check_concurrency_safe(tool_input)

    def partition_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """Partition tool calls into concurrency-safe batches and serial batches.

        Return format:
            [{"calls": [...], "concurrent": True/False}, ...]
        """
        batches: list[dict] = []
        current_safe: list[dict] = []

        for tc in tool_calls:
            name = tc.get("name", "")
            inp = tc.get("input", {})
            is_safe = self.is_concurrency_safe(name, inp)

            if is_safe:
                current_safe.append(tc)
            else:
                if current_safe:
                    batches.append({"calls": current_safe, "concurrent": True})
                    current_safe = []
                batches.append({"calls": [tc], "concurrent": False})

        if current_safe:
            batches.append({"calls": current_safe, "concurrent": True})

        return batches

    @property
    def count(self) -> int:
        return len(self._tools)
