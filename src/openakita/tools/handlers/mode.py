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

        # C8a §2.2 fix: Agent 对外暴露的当前 session 字段是 ``_current_session``
        # (TLS-keyed property, see agent.py:1140)，**不是** ``session``。原实现
        # ``getattr(self.agent, "session", None)`` 在生产环境永远返回 None，
        # 导致 switch_mode 总是走下面的 ``_pending_mode_switch`` 死路径
        # （而那个 flag 全代码库无人消费）。
        # 现在直读 ``_current_session``，确保 session_role 真的落到 Session
        # 对象上，下一轮 evaluate_via_v2 调 ``build_policy_context(session=...)``
        # 时就会把它 coerce 成 SessionRole 注入 PolicyContext.session_role。
        session = getattr(self.agent, "_current_session", None)
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

        # session 真不存在（一次性 task / 测试环境 / 早期 init）：明确告知
        # 用户而非假装"下一轮生效"——之前的死分支就是写一个无人读的 flag
        # 然后欺骗用户，C8a 删除。
        logger.warning(
            "switch_mode: no current session bound to agent; mode change cannot be "
            "applied. Caller must invoke from within chat_with_session(_stream) so "
            "Agent._current_session is set."
        )
        return (
            f"⚠️ 无法切换到 '{target_mode}' 模式：当前没有活动 session "
            f"绑定到 agent。请在对话上下文中调用此工具。"
        )


def create_handler(agent: "Agent"):
    handler = ModeHandler(agent)
    return handler.handle

