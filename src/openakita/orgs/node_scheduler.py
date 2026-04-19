"""
OrgNodeScheduler — Node Scheduled Task Runner

Manages independent scheduled tasks per node, supporting cron, fixed-interval,
and one-shot modes.  Includes adaptive frequency scaling: automatically reduces
check frequency when consecutive runs are clean, and immediately restores it
when an issue is detected.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import OrgRuntime

from .models import (
    NodeSchedule,
    NodeStatus,
    Organization,
    OrgStatus,
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
        for _key, task in list(self._tasks.items()):
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
                            target = target.replace(tzinfo=UTC)
                        now = datetime.now(UTC)
                        wait = (target - now).total_seconds()
                        if wait > 0:
                            await asyncio.sleep(wait)
                    await self._execute_schedule(org_id, node_id, sched)
                    break

                await asyncio.sleep(current_interval)

                org = self._runtime.get_org(org_id)
                if not org or org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                    continue

                node = org.get_node(node_id)
                if not node or node.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
                    continue

                result = await self._execute_schedule(org_id, node_id, sched)

                has_issue = "异常" in str(result) or "错误" in str(result) or "error" in str(result).lower()

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
                                f"down-scaling {current_interval}s -> {int(new_interval)}s "
                                f"({sched.consecutive_clean} consecutive clean runs)"
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
            f"[Scheduled Task] {sched.name}\n"
            f"Time: {_now_iso()}\n"
            f"Instruction: {sched.prompt}\n\n"
            f"Please execute the task above."
        )

        if sched.report_condition == "on_issue":
            prompt += (
                f"\n\nReporting rule: only report to {sched.report_to or 'supervisor'} when an issue is found. "
                f"If everything is normal, briefly record it in your private memory."
            )
        elif sched.report_condition == "always" and sched.report_to:
            prompt += f"\n\nAfter execution, please report the result to {sched.report_to}."

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
