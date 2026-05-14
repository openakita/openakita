"""
Sleep 工具处理器

可中断的 asyncio.sleep，不占 shell 进程。
参考 CC SleepTool。

# ApprovalClass checklist (新增 / 修改工具时必读)
# 1. 在本文件 Handler 类的 TOOLS 列表加新工具名
# 2. 在同 Handler 类的 TOOL_CLASSES 字典加 ApprovalClass 显式声明
#    （或在 agent.py:_init_handlers 的 register() 调用里加 tool_classes={...}）
# 3. 行为依赖参数 → 在 policy_v2/classifier.py:_refine_with_params 加分支
# 4. 跑 pytest tests/unit/test_classifier_completeness.py 验证
# 详见 docs/policy_v2_research.md §4.21
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ...core.policy_v2 import ApprovalClass

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

MAX_SLEEP = 300  # 5 minutes


class SleepHandler:
    TOOLS = ["sleep"]
    TOOL_CLASSES = {"sleep": ApprovalClass.EXEC_LOW_RISK}

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "sleep":
            return await self._sleep(params)
        return f"Unknown tool: {tool_name}"

    async def _sleep(self, params: dict[str, Any]) -> str:
        seconds = params.get("seconds", 0)
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return "sleep requires a numeric 'seconds' parameter."

        if seconds <= 0:
            return "Sleep duration must be positive."
        seconds = min(seconds, MAX_SLEEP)

        logger.info(f"[Sleep] Sleeping for {seconds}s")
        try:
            await asyncio.sleep(seconds)
            return f"Slept for {seconds} seconds."
        except asyncio.CancelledError:
            logger.info("[Sleep] Sleep interrupted by user")
            return "Sleep interrupted."


def create_handler(agent: "Agent"):
    handler = SleepHandler(agent)
    return handler.handle

