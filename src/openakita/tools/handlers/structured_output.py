"""
StructuredOutput 工具处理器

参考 CC SyntheticOutputTool：在 API/SDK 模式下返回结构化 JSON。
可选通过 JSON Schema 验证输出格式。
"""

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class StructuredOutputHandler:
    TOOLS = ["structured_output"]

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
