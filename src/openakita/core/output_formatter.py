"""
多格式输出 (Headless 模式)

参考 Claude Code 的 output format 设计:
- text: 默认交互式输出
- json: 完整对话 JSON
- stream-json: NDJSON 流式输出
"""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from typing import TextIO


class OutputFormatter(ABC):
    """输出格式化器基类。"""

    @abstractmethod
    def format_message(self, role: str, content: str, **kwargs) -> str:
        """格式化单条消息。"""
        pass

    @abstractmethod
    def format_tool_use(self, tool_name: str, tool_input: dict) -> str:
        """格式化工具调用。"""
        pass

    @abstractmethod
    def format_tool_result(self, tool_name: str, result: str, is_error: bool = False) -> str:
        """格式化工具结果。"""
        pass

    @abstractmethod
    def format_final(self, conversation: list[dict]) -> str:
        """格式化最终输出。"""
        pass


class TextFormatter(OutputFormatter):
    """文本格式化器（默认）。"""

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
    """JSON 格式化器（完整对话）。"""

    def format_message(self, role: str, content: str, **kwargs) -> str:
        return ""  # Suppress intermediate output

    def format_tool_use(self, tool_name: str, tool_input: dict) -> str:
        return ""

    def format_tool_result(self, tool_name: str, result: str, is_error: bool = False) -> str:
        return ""

    def format_final(self, conversation: list[dict]) -> str:
        return json.dumps(conversation, ensure_ascii=False, indent=2, default=str)


class StreamJSONFormatter(OutputFormatter):
    """NDJSON 流式格式化器。"""

    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self._stream = stream

    def _emit(self, event: dict) -> str:
        line = json.dumps(event, ensure_ascii=False, default=str)
        return line

    def format_message(self, role: str, content: str, **kwargs) -> str:
        return self._emit({
            "type": "message",
            "role": role,
            "content": content,
            **kwargs,
        })

    def format_tool_use(self, tool_name: str, tool_input: dict) -> str:
        return self._emit({
            "type": "tool_use",
            "name": tool_name,
            "input": tool_input,
        })

    def format_tool_result(self, tool_name: str, result: str, is_error: bool = False) -> str:
        return self._emit({
            "type": "tool_result",
            "name": tool_name,
            "content": result[:2000],
            "is_error": is_error,
        })

    def format_final(self, conversation: list[dict]) -> str:
        return self._emit({"type": "done"})


def create_formatter(format_type: str = "text") -> OutputFormatter:
    """创建指定类型的格式化器。

    Args:
        format_type: 'text' | 'json' | 'stream-json'
    """
    formatters = {
        "text": TextFormatter,
        "json": JSONFormatter,
        "stream-json": StreamJSONFormatter,
    }
    cls = formatters.get(format_type, TextFormatter)
    return cls()
