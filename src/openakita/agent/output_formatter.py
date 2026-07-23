"""Multi-format output for headless / non-interactive runs.

Three formatters cover the use cases the CLI / SDK consumers need:

* :class:`TextFormatter` — emoji-prefixed human-readable output for
  interactive terminals. Default.
* :class:`JSONFormatter` — emit nothing during the run, return the
  full conversation as a JSON dict at the end. Used by SDK callers
  that want a single artifact.
* :class:`StreamJSONFormatter` — newline-delimited JSON events
  (``message``, ``tool_use``, ``tool_result``, ``done``). Used by
  CI, scripts, and any consumer that wants to parse output as it
  arrives.
"""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from typing import TextIO

__all__ = [
    "JSONFormatter",
    "OutputFormatter",
    "StreamJSONFormatter",
    "TextFormatter",
    "create_formatter",
]


class OutputFormatter(ABC):
    """Base contract every concrete formatter implements."""

    @abstractmethod
    def format_message(self, role: str, content: str, **kwargs) -> str:
        """Format a single chat message."""

    @abstractmethod
    def format_tool_use(self, tool_name: str, tool_input: dict) -> str:
        """Format a tool invocation."""

    @abstractmethod
    def format_tool_result(self, tool_name: str, result: str, is_error: bool = False) -> str:
        """Format the tool's result (possibly an error)."""

    @abstractmethod
    def format_final(self, conversation: list[dict]) -> str:
        """Format the closing artifact for the whole run."""


class TextFormatter(OutputFormatter):
    """Emoji-prefixed plain-text formatter (default)."""

    def format_message(self, role: str, content: str, **kwargs) -> str:
        prefix = {"assistant": "🤖", "user": "👤", "system": "⚙️"}.get(role, "📝")
        return f"{prefix} {content}"

    def format_tool_use(self, tool_name: str, tool_input: dict) -> str:
        args = json.dumps(tool_input, ensure_ascii=False, indent=2)
        return f"🔧 {tool_name}({args})"

    def format_tool_result(self, tool_name: str, result: str, is_error: bool = False) -> str:
        icon = "❌" if is_error else "✅"
        preview = result[:500] if len(result) > 500 else result
        return f"{icon} {tool_name}: {preview}"

    def format_final(self, conversation: list[dict]) -> str:
        return ""


class JSONFormatter(OutputFormatter):
    """Suppress intermediate output; emit a single JSON artifact at end."""

    def format_message(self, role: str, content: str, **kwargs) -> str:
        return ""

    def format_tool_use(self, tool_name: str, tool_input: dict) -> str:
        return ""

    def format_tool_result(self, tool_name: str, result: str, is_error: bool = False) -> str:
        return ""

    def format_final(self, conversation: list[dict]) -> str:
        return json.dumps(conversation, ensure_ascii=False, indent=2, default=str)


class StreamJSONFormatter(OutputFormatter):
    """Newline-delimited JSON events for streaming consumers."""

    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self._stream = stream

    def _emit(self, event: dict) -> str:
        return json.dumps(event, ensure_ascii=False, default=str)

    def format_message(self, role: str, content: str, **kwargs) -> str:
        return self._emit(
            {
                "type": "message",
                "role": role,
                "content": content,
                **kwargs,
            }
        )

    def format_tool_use(self, tool_name: str, tool_input: dict) -> str:
        return self._emit(
            {
                "type": "tool_use",
                "name": tool_name,
                "input": tool_input,
            }
        )

    def format_tool_result(self, tool_name: str, result: str, is_error: bool = False) -> str:
        return self._emit(
            {
                "type": "tool_result",
                "name": tool_name,
                "content": result[:2000],
                "is_error": is_error,
            }
        )

    def format_final(self, conversation: list[dict]) -> str:
        return self._emit({"type": "done"})


def create_formatter(format_type: str = "text") -> OutputFormatter:
    """Return a formatter for ``format_type`` ('text' / 'json' / 'stream-json').

    Unknown values fall back to :class:`TextFormatter` so a typo does
    not break a run; the caller can validate the input upstream when
    strictness matters.
    """
    formatters = {
        "text": TextFormatter,
        "json": JSONFormatter,
        "stream-json": StreamJSONFormatter,
    }
    cls = formatters.get(format_type, TextFormatter)
    return cls()
