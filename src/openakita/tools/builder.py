"""
工具工厂

参考 Claude Code 的 buildTool + ToolDef 模式:
- 声明式工具定义
- 自动填充默认值
- 统一注册入口
- 并发安全/只读/破坏性标记
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
    """声明式工具定义。"""

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
        """检查给定输入下是否并发安全。"""
        if callable(self.is_concurrency_safe):
            return self.is_concurrency_safe(tool_input)
        return bool(self.is_concurrency_safe)

    def check_read_only(self, tool_input: dict) -> bool:
        """检查给定输入下是否只读。"""
        if callable(self.is_read_only):
            return self.is_read_only(tool_input)
        return bool(self.is_read_only)

    def check_destructive(self, tool_input: dict) -> bool:
        """检查给定输入下是否具有破坏性。"""
        if callable(self.is_destructive):
            return self.is_destructive(tool_input)
        return bool(self.is_destructive)

    def check_enabled(self) -> bool:
        """检查工具是否启用。"""
        if callable(self.is_enabled):
            return self.is_enabled()
        return bool(self.is_enabled)


def build_tool(tool_def: ToolDef) -> dict:
    """从 ToolDef 生成完整的工具注册信息。

    返回兼容现有 SystemHandlerRegistry 的 dict 格式。
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
    """生成 LLM function calling 用的 schema。"""
    return {
        "name": tool_def.name,
        "description": tool_def.description,
        "input_schema": tool_def.input_schema,
    }


class ToolRegistry:
    """基于 ToolDef 的工具注册表。

    与现有 SystemHandlerRegistry 并存，逐步迁移。
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef) -> None:
        """注册一个工具。"""
        self._tools[tool_def.name] = tool_def
        logger.debug("Registered tool: %s (category=%s)", tool_def.name, tool_def.category)

    def get(self, name: str) -> ToolDef | None:
        """获取工具定义。"""
        return self._tools.get(name)

    def get_enabled_tools(self) -> list[ToolDef]:
        """获取所有启用的工具。"""
        return [t for t in self._tools.values() if t.check_enabled()]

    def get_schemas(self, *, sorted_for_cache: bool = True) -> list[dict]:
        """获取所有启用工具的 LLM schema。

        Args:
            sorted_for_cache: 按名称排序保证 prompt cache 稳定性
        """
        tools = self.get_enabled_tools()
        if sorted_for_cache:
            tools = sorted(tools, key=lambda t: t.name)
        return [build_tool_schema(t) for t in tools]

    def is_concurrency_safe(self, name: str, tool_input: dict) -> bool:
        """查询工具在给定输入下是否并发安全。"""
        tool = self._tools.get(name)
        if not tool:
            return False
        return tool.check_concurrency_safe(tool_input)

    def partition_tool_calls(
        self, tool_calls: list[dict]
    ) -> list[dict]:
        """将工具调用分区为并发安全批次和串行批次。

        返回格式:
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
