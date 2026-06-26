"""Tool definition helpers for tool plugins."""

from __future__ import annotations

from typing import Any


def tool_definition(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an OpenAI-compatible tool definition dict.

    Example::

        TOOLS = [
            tool_definition(
                name="search_notes",
                description="Search user's notes by keyword",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                    },
                    "required": ["query"],
                },
            ),
        ]
    """
    defn: dict[str, Any] = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
        },
    }
    return defn


class ToolHandler:
    """Protocol-like base for tool handlers.

    Plugin authors can either pass a plain async function to
    ``api.register_tools(definitions, handler)`` or subclass this for
    more structured dispatch.

    The handler callable receives ``(tool_name: str, arguments: dict)``
    and must return a string result.
    """

    async def handle(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Dispatch a tool call. Override in subclass."""
        raise NotImplementedError(f"No handler for tool: {tool_name}")

    async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> str:
        return await self.handle(tool_name, arguments)
