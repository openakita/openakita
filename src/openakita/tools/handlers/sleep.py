"""
Sleep 工具处理器

可中断的 asyncio.sleep，不占 shell 进程。
参考 CC SleepTool。
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

MAX_SLEEP = 300  # 5 minutes


class SleepHandler:
    TOOLS = ["sleep"]

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
