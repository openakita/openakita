"""BPStateManager — BP 实例生命周期与状态管理。

Key decisions:
- 内存为主，通过 Session.metadata["bp_state"] 持久化 (NOT "_bp_state")
- 独立于 AgentState/TaskState/SessionContext
- 线程安全（同一 session 的并发操作通过 GIL 保护，异步通过单线程 eventloop）
"""

from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

from .models import (
    BPInstanceSnapshot,
    BPStatus,
    PendingContextSwitch,
    RunMode,
    SubtaskStatus,
)

if TYPE_CHECKING:
    from .models import BestPracticeConfig

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_TURNS = 3


class BPStateManager:
    """管理所有 BP 实例的内存状态。"""

    def __init__(self) -> None:
        self._instances: dict[str, BPInstanceSnapshot] = {}
        self._pending_switches: dict[str, PendingContextSwitch] = {}  # session_id → switch
        self._cooldowns: dict[str, int] = {}  # session_id → remaining turns

    # ── Instance lifecycle ─────────────────────────────────────

    def create_instance(
        self,
        bp_config: BestPracticeConfig,
        session_id: str,
        initial_input: dict[str, Any] | None = None,
        run_mode: RunMode = RunMode.MANUAL,
    ) -> str:
        """创建新的 BP 实例。返回 instance_id。"""
        instance_id = BPInstanceSnapshot.new_instance_id()

        # 初始化所有子任务状态
        statuses = {s.id: SubtaskStatus.PENDING.value for s in bp_config.subtasks}

        snap = BPInstanceSnapshot(
            bp_id=bp_config.id,
            instance_id=instance_id,
            session_id=session_id,
            status=BPStatus.ACTIVE,
            created_at=time.time(),
            run_mode=run_mode,
            subtask_statuses=statuses,
            initial_input=dict(initial_input or {}),
            bp_config=bp_config,
        )
        self._instances[instance_id] = snap
        logger.info(f"[BP] Created instance {instance_id} for '{bp_config.id}' in session {session_id}")
        return instance_id

    def suspend(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap and snap.status == BPStatus.ACTIVE:
            snap.status = BPStatus.SUSPENDED
            snap.suspended_at = time.time()

    def resume(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap and snap.status == BPStatus.SUSPENDED:
            snap.status = BPStatus.ACTIVE
            snap.suspended_at = None

    def complete(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap:
            snap.status = BPStatus.COMPLETED
            snap.completed_at = time.time()

    def cancel(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap and snap.status in (BPStatus.ACTIVE, BPStatus.SUSPENDED):
            snap.status = BPStatus.CANCELLED
            snap.completed_at = time.time()

    # ── Subtask operations ─────────────────────────────────────

    def advance_subtask(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap:
            snap.current_subtask_index += 1

    def update_subtask_status(self, instance_id: str, subtask_id: str, status: SubtaskStatus) -> None:
        snap = self._instances.get(instance_id)
        if snap:
            snap.subtask_statuses[subtask_id] = status.value

    def update_subtask_output(self, instance_id: str, subtask_id: str, output: dict[str, Any]) -> None:
        snap = self._instances.get(instance_id)
        if snap:
            snap.subtask_outputs[subtask_id] = dict(output)

    def merge_subtask_output(self, instance_id: str, subtask_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """深度合并 changes 到现有输出。返回合并后结果。"""
        snap = self._instances.get(instance_id)
        if not snap:
            return {}
        existing = snap.subtask_outputs.get(subtask_id, {})
        merged = self._deep_merge(existing, changes)
        snap.subtask_outputs[subtask_id] = merged
        return merged

    def mark_downstream_stale(
        self, instance_id: str, from_subtask_id: str, bp_config: BestPracticeConfig,
    ) -> list[str]:
        """将 from_subtask_id 之后的所有 DONE 子任务标记为 STALE。返回受影响的 subtask_id 列表。"""
        snap = self._instances.get(instance_id)
        if not snap:
            return []

        stale_ids: list[str] = []
        found = False
        for s in bp_config.subtasks:
            if s.id == from_subtask_id:
                found = True
                continue
            if found and snap.subtask_statuses.get(s.id) == SubtaskStatus.DONE.value:
                snap.subtask_statuses[s.id] = SubtaskStatus.STALE.value
                stale_ids.append(s.id)
        return stale_ids

    # ── Queries ────────────────────────────────────────────────

    def get(self, instance_id: str) -> BPInstanceSnapshot | None:
        return self._instances.get(instance_id)

    def get_active(self, session_id: str) -> BPInstanceSnapshot | None:
        """返回 session 中唯一的 ACTIVE 实例。"""
        for snap in self._instances.values():
            if snap.session_id == session_id and snap.status == BPStatus.ACTIVE:
                return snap
        return None

    def get_all_for_session(self, session_id: str) -> list[BPInstanceSnapshot]:
        return [s for s in self._instances.values() if s.session_id == session_id]

    def get_status_table(self, session_id: str) -> str:
        """生成供 system prompt 注入的状态概览表。"""
        instances = self.get_all_for_session(session_id)
        if not instances:
            return ""

        lines = ["| Instance | BP | Status | Progress | RunMode |", "| --- | --- | --- | --- | --- |"]
        for inst in instances:
            bp_name = inst.bp_config.name if inst.bp_config else inst.bp_id
            total = len(inst.subtask_statuses)
            done = sum(1 for v in inst.subtask_statuses.values() if v == SubtaskStatus.DONE.value)
            progress = f"{done}/{total}"
            lines.append(
                f"| {inst.instance_id} | {bp_name} | {inst.status.value} | {progress} | {inst.run_mode.value} |"
            )
        return "\n".join(lines)

    # ── PendingContextSwitch ──────────────────────────────────

    def set_pending_switch(self, session_id: str, switch: PendingContextSwitch) -> None:
        self._pending_switches[session_id] = switch

    def consume_pending_switch(self, session_id: str) -> PendingContextSwitch | None:
        return self._pending_switches.pop(session_id, None)

    def has_pending_switch(self, session_id: str) -> bool:
        return session_id in self._pending_switches

    # ── Cooldown ───────────────────────────────────────────────

    def set_cooldown(self, session_id: str, turns: int = DEFAULT_COOLDOWN_TURNS) -> None:
        self._cooldowns[session_id] = turns

    def tick_cooldown(self, session_id: str) -> int:
        """递减并返回剩余轮数。"""
        remaining = self._cooldowns.get(session_id, 0)
        if remaining > 0:
            remaining -= 1
            self._cooldowns[session_id] = remaining
        return remaining

    def get_cooldown(self, session_id: str) -> int:
        return self._cooldowns.get(session_id, 0)

    # ── Persistence ────────────────────────────────────────────

    def serialize_for_session(self, session_id: str) -> dict[str, Any]:
        """序列化 session 的所有实例 → 可存入 Session.metadata["bp_state"]。"""
        instances = self.get_all_for_session(session_id)
        return {
            "version": 1,
            "instances": [inst.serialize() for inst in instances],
            "cooldown": self._cooldowns.get(session_id, 0),
        }

    def restore_from_dict(
        self,
        session_id: str,
        data: dict[str, Any],
        config_map: dict[str, BestPracticeConfig] | None = None,
    ) -> int:
        """从序列化 dict 恢复实例。返回恢复的实例数。"""
        if not data:
            return 0
        config_map = config_map or {}
        count = 0
        for inst_data in data.get("instances", []):
            snap = BPInstanceSnapshot.deserialize(inst_data)
            snap.bp_config = config_map.get(snap.bp_id)
            self._instances[snap.instance_id] = snap
            count += 1
        if "cooldown" in data:
            self._cooldowns[session_id] = data["cooldown"]
        return count

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _deep_merge(base: dict, overlay: dict) -> dict:
        """递归合并 overlay 到 base。数组完整替换。"""
        result = dict(base)
        for k, v in overlay.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = BPStateManager._deep_merge(result[k], v)
            else:
                result[k] = v
        return result
