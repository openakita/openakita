"""
OrgNodeScheduler — 节点定时任务调度

管理每个节点的独立定时任务，支持 cron、固定间隔、一次性三种模式。
包含智能调频：连续无异常时自动降频，发现异常立即恢复。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import OrgRuntime

from .models import (
    NodeSchedule,
    NodeStatus,
    OrgStatus,
    Organization,
    ScheduleType,
    _now_iso,
)

logger = logging.getLogger(__name__)

CLEAN_THRESHOLD = 5
FREQUENCY_MULTIPLIER = 1.5
MAX_FREQUENCY_FACTOR = 4.0
RECHECK_DELAY = 300


class OrgNodeScheduler:
    """Manages per-node scheduled tasks for all active organizations."""

    def __init__(self, runtime: OrgRuntime) -> None:
        self._runtime = runtime
        self._tasks: dict[str, asyncio.Task] = {}

    async def start_for_org(self, org: Organization) -> None:
        """Start schedule loops for all nodes in an organization."""
        for node in org.nodes:
            schedules = self._runtime._manager.get_node_schedules(org.id, node.id)
            for sched in schedules:
                if sched.enabled:
                    self._start_schedule(org.id, node.id, sched)

    async def stop_for_org(self, org_id: str) -> None:
        prefix = f"{org_id}:"
        to_cancel = [k for k in self._tasks if k.startswith(prefix)]
        for key in to_cancel:
            task = self._tasks.pop(key)
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def stop_all(self) -> None:
        for key, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._tasks.clear()

    async def reload_node_schedules(self, org_id: str, node_id: str) -> None:
        """Reload schedules for a specific node (after CRUD operations)."""
        prefix = f"{org_id}:{node_id}:"
        for key in [k for k in self._tasks if k.startswith(prefix)]:
            task = self._tasks.pop(key)
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        schedules = self._runtime._manager.get_node_schedules(org_id, node_id)
        for sched in schedules:
            if sched.enabled:
                self._start_schedule(org_id, node_id, sched)

    async def trigger_once(self, org_id: str, node_id: str, schedule_id: str) -> dict:
        """Manually trigger a schedule execution."""
        schedules = self._runtime._manager.get_node_schedules(org_id, node_id)
        sched = next((s for s in schedules if s.id == schedule_id), None)
        if not sched:
            return {"error": "Schedule not found"}
        return await self._execute_schedule(org_id, node_id, sched)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_schedule(self, org_id: str, node_id: str, sched: NodeSchedule) -> None:
        key = f"{org_id}:{node_id}:{sched.id}"
        if key in self._tasks:
            return
        task = asyncio.create_task(self._schedule_loop(org_id, node_id, sched))
        self._tasks[key] = task

    async def _schedule_loop(self, org_id: str, node_id: str, sched: NodeSchedule) -> None:
        """Main loop for a single scheduled task."""
        base_interval = sched.interval_s if sched.interval_s and sched.interval_s > 0 else 3600
        current_interval = base_interval

        while True:
            try:
                if sched.schedule_type == ScheduleType.ONCE:
                    if sched.run_at:
                        target = datetime.fromisoformat(sched.run_at)
                        if target.tzinfo is None:
                            target = target.replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        wait = (target - now).total_seconds()
                        if wait > 0:
                            await asyncio.sleep(wait)
                    await self._execute_schedule(org_id, node_id, sched)
                    # 清理自身在 _tasks 中的条目，防止内存泄漏
                    key = f"{org_id}:{node_id}:{sched.id}"
                    self._tasks.pop(key, None)
                    break

                await asyncio.sleep(current_interval)

                org = self._runtime.get_org(org_id)
                if not org or org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                    continue

                node = org.get_node(node_id)
                if not node or node.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
                    continue

                result = await self._execute_schedule(org_id, node_id, sched)

                result_text = str(result.get("result", "")) if isinstance(result, dict) else str(result)
                keyword_check = "异常" in result_text or "错误" in result_text or "error" in result_text.lower()
                if isinstance(result, dict) and "error" in result:
                    has_issue = True
                elif isinstance(result, dict) and "success" in result:
                    has_issue = result["success"] is False
                else:
                    has_issue = keyword_check

                if has_issue:
                    sched.consecutive_clean = 0
                    current_interval = base_interval
                    self._save_schedule(org_id, node_id, sched)
                    await asyncio.sleep(RECHECK_DELAY)
                    await self._execute_schedule(org_id, node_id, sched)
                else:
                    sched.consecutive_clean += 1
                    if sched.consecutive_clean >= CLEAN_THRESHOLD:
                        new_interval = min(
                            current_interval * FREQUENCY_MULTIPLIER,
                            base_interval * MAX_FREQUENCY_FACTOR,
                        )
                        if new_interval != current_interval:
                            logger.info(
                                f"[Scheduler] {node_id}/{sched.name}: "
                                f"降频 {current_interval}s -> {int(new_interval)}s "
                                f"(连续{sched.consecutive_clean}次无异常)"
                            )
                            current_interval = new_interval
                    self._save_schedule(org_id, node_id, sched)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Scheduler] Error in {node_id}/{sched.name}: {e}")
                await asyncio.sleep(60)

    async def _execute_schedule(
        self, org_id: str, node_id: str, sched: NodeSchedule
    ) -> dict:
        """Execute a single scheduled task."""
        es = self._runtime.get_event_store(org_id)
        es.emit("schedule_triggered", node_id, {
            "schedule_id": sched.id,
            "name": sched.name,
        })

        prompt = (
            f"[定时任务] {sched.name}\n"
            f"时间: {_now_iso()}\n"
            f"指令: {sched.prompt}\n\n"
            f"请执行上述任务。"
        )

        if sched.report_condition == "on_issue":
            prompt += (
                f"\n\n汇报规则：仅在发现异常/问题时向 {sched.report_to or '上级'} 汇报。"
                f"如果一切正常，简要记录到你的私有记忆即可。"
            )
        elif sched.report_condition == "always" and sched.report_to:
            prompt += f"\n\n执行完毕后请向 {sched.report_to} 汇报结果。"

        result = await self._runtime.send_command(org_id, node_id, prompt)

        sched.last_run_at = _now_iso()
        result_text = result.get("result", "")
        sched.last_result_summary = result_text[:200] if result_text else None
        self._save_schedule(org_id, node_id, sched)

        es.emit("schedule_completed", node_id, {
            "schedule_id": sched.id,
            "result_preview": result_text[:100] if result_text else "",
        })

        return result

    def _save_schedule(self, org_id: str, node_id: str, sched: NodeSchedule) -> None:
        """Persist schedule state changes."""
        schedules = self._runtime._manager.get_node_schedules(org_id, node_id)
        for i, s in enumerate(schedules):
            if s.id == sched.id:
                schedules[i] = sched
                break
        self._runtime._manager.save_node_schedules(org_id, node_id, schedules)
