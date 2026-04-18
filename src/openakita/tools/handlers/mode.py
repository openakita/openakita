"""
Mode handler

Mode switching:
- switch_mode: switch interaction mode (agent/plan/ask)
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class ModeHandler:
    TOOLS = ["switch_mode"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "switch_mode":
            return await self._switch_mode(params)
        return f"❌ Unknown mode tool: {tool_name}"

    async def _switch_mode(self, params: dict) -> str:
        target_mode = params.get("target_mode", "")
        reason = params.get("reason", "")

        valid_modes = ("plan", "ask", "agent")
        if target_mode not in valid_modes:
            return f"❌ Invalid mode: '{target_mode}'. Valid options: {', '.join(valid_modes)}"

        session = getattr(self.agent, "session", None)
        if session and hasattr(session, "mode"):
            current_mode = session.mode
            if current_mode == target_mode:
                return f"Already in {target_mode} mode."

            session.mode = target_mode
            logger.info(
                f"Mode switched: {current_mode} → {target_mode}"
                + (f" (reason: {reason})" if reason else "")
            )

            mode_labels = {"plan": "Plan (planning)", "ask": "Ask (Q&A)", "agent": "Agent (execution)"}
            label = mode_labels.get(target_mode, target_mode)
            msg = f"Switched to {label} mode."
            if reason:
                msg += f"\nReason: {reason}"
            return msg

        logger.warning("No session found for mode switch, setting flag for next iteration")
        self.agent._pending_mode_switch = target_mode
        return f"Mode switch to '{target_mode}' will take effect on the next iteration."


def create_handler(agent: "Agent"):
    handler = ModeHandler(agent)
    return handler.handle
