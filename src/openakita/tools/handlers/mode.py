"""
Mode 处理器

模式切换：
- switch_mode: 切换交互模式 (agent/plan/ask)
"""

import logging
from typing import TYPE_CHECKING, Any

from ...core.policy_v2 import ApprovalClass

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class ModeHandler:
    TOOLS = ["switch_mode"]
    TOOL_CLASSES = {"switch_mode": ApprovalClass.CONTROL_PLANE}

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
            return f"❌ 无效模式: '{target_mode}'。可选: {', '.join(valid_modes)}"

        session = getattr(self.agent, "session", None)
        # C8 §2.2 fix: ``Session`` dataclass 没有 ``mode`` 字段，老实现 hasattr
        # 永远 False → switch_mode 静默失败。改写 ``session.session_role``，
        # adapter.build_policy_context 把它 coerce 成 SessionRole 注入下一轮
        # PolicyContext.session_role，让矩阵决策真用上新 mode。
        if session is not None and hasattr(session, "session_role"):
            current_mode = session.session_role or "agent"
            if current_mode == target_mode:
                return f"Already in {target_mode} mode."

            session.session_role = target_mode
            logger.info(
                f"Mode switched: {current_mode} → {target_mode}"
                + (f" (reason: {reason})" if reason else "")
            )

            mode_labels = {"plan": "Plan（规划）", "ask": "Ask（问答）", "agent": "Agent（执行）"}
            label = mode_labels.get(target_mode, target_mode)
            msg = f"已切换到 {label} 模式。"
            if reason:
                msg += f"\n原因: {reason}"
            return msg

        logger.warning("No session found for mode switch, setting flag for next iteration")
        self.agent._pending_mode_switch = target_mode
        return f"Mode switch to '{target_mode}' will take effect on the next iteration."


def create_handler(agent: "Agent"):
    handler = ModeHandler(agent)
    return handler.handle

