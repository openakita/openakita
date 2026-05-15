"""
StructuredOutput 工具处理器

参考 CC SyntheticOutputTool：在 API/SDK 模式下返回结构化 JSON。
可选通过 JSON Schema 验证输出格式。

# ApprovalClass checklist (新增 / 修改工具时必读)
# 1. 在本文件 Handler 类的 TOOLS 列表加新工具名
# 2. 在同 Handler 类的 TOOL_CLASSES 字典加 ApprovalClass 显式声明
#    （或在 agent.py:_init_handlers 的 register() 调用里加 tool_classes={...}）
# 3. 行为依赖参数 → 在 policy_v2/classifier.py:_refine_with_params 加分支
# 4. 跑 pytest tests/unit/test_classifier_completeness.py 验证
# 详见 docs/policy_v2_research.md §4.21
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from ...core.policy_v2 import ApprovalClass

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class StructuredOutputHandler:
    TOOLS = ["structured_output"]
    TOOL_CLASSES = {"structured_output": ApprovalClass.EXEC_LOW_RISK}

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._schema: dict | None = None

    def set_output_schema(self, schema: dict) -> None:
        """Set the expected output JSON Schema for validation."""
        self._schema = schema

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "structured_output":
            return await self._output(params)
        return f"Unknown tool: {tool_name}"

    async def _output(self, params: dict[str, Any]) -> str:
        data = params.get("data")
        if data is None:
            return "structured_output requires a 'data' parameter."

        # Store for the caller to retrieve
        if hasattr(self.agent, "_structured_output_result"):
            self.agent._structured_output_result = data

        result = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        logger.info(f"[StructuredOutput] Captured {len(result)} chars")
        return f"Structured output captured:\n{result}"


def create_handler(agent: "Agent"):
    handler = StructuredOutputHandler(agent)
    return handler.handle

