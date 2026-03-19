"""ContextBridge — 上下文压缩与恢复。

负责在 BP 实例切换时:
1. 压缩当前上下文 → context_summary (LLM 调用)
2. 恢复目标实例的上下文 → 注入恢复消息
3. 管理 PendingContextSwitch 生命周期
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from .models import PendingContextSwitch

if TYPE_CHECKING:
    from .state_manager import BPStateManager

logger = logging.getLogger(__name__)


class ContextBridge:
    """管理 BP 实例间的上下文切换。"""

    def __init__(self, state_manager: BPStateManager | None = None) -> None:
        self._state_manager = state_manager

    def set_state_manager(self, state_manager: BPStateManager) -> None:
        self._state_manager = state_manager

    async def execute_pending_switch(self, session_id: str, brain: Any = None) -> bool:
        """消费 PendingContextSwitch，执行上下文切换。

        Called from Agent._pre_reasoning_hook() 在安全时间点。

        Returns: True if switch was executed, False if no pending switch.
        """
        if not self._state_manager:
            return False

        switch = self._state_manager.consume_pending_switch(session_id)
        if not switch:
            return False

        logger.info(
            f"[BP] Executing context switch: "
            f"{switch.suspended_instance_id} → {switch.target_instance_id}"
        )

        # 1. 压缩当前上下文
        if switch.suspended_instance_id and brain:
            summary = await self._compress_context(brain)
            suspended = self._state_manager.get(switch.suspended_instance_id)
            if suspended:
                suspended.context_summary = summary

        # 2. 恢复目标实例的上下文
        target = self._state_manager.get(switch.target_instance_id)
        if target and brain:
            self._restore_context(brain, target)

        return True

    async def _compress_context(self, brain: Any) -> str:
        """压缩 Brain.Context.messages 为简短摘要。

        在生产实现中调用 LLM summarize。简化版直接截取。
        """
        try:
            messages = getattr(brain, "messages", [])
            if not messages:
                return ""
            # 取最后几条消息的内容摘要
            summaries = []
            for msg in messages[-5:]:
                content = msg.get("content", "")
                if isinstance(content, str) and content:
                    summaries.append(content[:200])
            return " | ".join(summaries)
        except Exception as e:
            logger.warning(f"[BP] Context compression failed: {e}")
            return ""

    def _restore_context(self, brain: Any, snap: Any) -> None:
        """将目标实例的 context_summary 注入为恢复消息。"""
        if not snap.context_summary:
            return
        try:
            recovery_msg = (
                f"[任务恢复] 你正在继续执行最佳实践任务。\n"
                f"之前的上下文摘要: {snap.context_summary}\n"
                f"请继续当前子任务的执行。"
            )
            # 注入为 system 或 user message
            if hasattr(brain, "messages"):
                brain.messages.append({"role": "user", "content": recovery_msg})
        except Exception as e:
            logger.warning(f"[BP] Context restore failed: {e}")

    def build_recovery_message(self, snap: Any) -> str:
        """生成任务恢复注入消息。用于前端展示或 prompt 注入。"""
        bp_name = snap.bp_config.name if snap.bp_config else snap.bp_id
        return (
            f"[任务恢复] 最佳实践「{bp_name}」\n"
            f"当前进度: 第 {snap.current_subtask_index + 1} 步\n"
            f"上下文摘要: {snap.context_summary or '(无)'}"
        )
