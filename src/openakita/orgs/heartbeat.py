"""
OrgHeartbeat — Heartbeat scheduling, standup/report generation

Periodically triggers the top-level Agent to review organization status,
supporting automatic standup meetings and report generation.
Cascaded LLM call depth is limited via heartbeat_max_cascade_depth.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import OrgRuntime

from .models import NodeStatus, Organization, OrgStatus, _now_iso

logger = logging.getLogger(__name__)


class OrgHeartbeat:
    """Heartbeat scheduler for organizations."""

    def __init__(self, runtime: OrgRuntime) -> None:
        self._runtime = runtime
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}
        self._standup_tasks: dict[str, asyncio.Task] = {}
        self._last_activity: dict[str, float] = {}
        self._tasks_since_review: dict[str, int] = {}

    def record_activity(self, org_id: str) -> None:
        """Record that an org had activity (called from runtime on task events)."""
        self._last_activity[org_id] = time.monotonic()
        self._tasks_since_review[org_id] = self._tasks_since_review.get(org_id, 0) + 1

    async def _recover_error_nodes(self, org: Organization) -> None:
        """Reset long-stuck ERROR nodes to IDLE during heartbeat.

        Non-root nodes in ERROR may never be activated again, leaving them
        permanently broken. Each heartbeat clears their agent cache and
        resets them so they can accept new tasks.
        """
        recovered = 0
        for node in org.nodes:
            if node.status == NodeStatus.ERROR:
                self._runtime._set_node_status(
                    org, node, NodeStatus.IDLE, "heartbeat_recovery"
                )
                self._runtime._agent_cache.pop(f"{org.id}:{node.id}", None)
                recovered += 1
        if recovered:
            await self._runtime._save_org(org)

    def _compute_adaptive_interval(self, org: Organization) -> float:
        """Compute heartbeat interval based on recent activity level."""
        base = org.heartbeat_interval_s
        last = self._last_activity.get(org.id, 0)
        if last <= 0:
            return base

        idle_secs = time.monotonic() - last
        if idle_secs < 300:
            return max(base * 0.17, 300)
        elif idle_secs < 900:
            return max(base * 0.33, 600)
        elif idle_secs < 3600:
            return base
        else:
            return min(base * 2, 3600)

    async def start_for_org(self, org: Organization) -> None:
        """Start heartbeat and standup schedules for an organization."""
        if org.heartbeat_enabled and org.id not in self._heartbeat_tasks:
            task = asyncio.create_task(self._heartbeat_loop(org))
            self._heartbeat_tasks[org.id] = task
            logger.info(f"[Heartbeat] Started heartbeat for {org.name} (interval={org.heartbeat_interval_s}s)")

        if org.standup_enabled and org.id not in self._standup_tasks:
            task = asyncio.create_task(self._standup_loop(org))
            self._standup_tasks[org.id] = task
            logger.info(f"[Heartbeat] Started standup for {org.name} (cron={org.standup_cron})")

    async def stop_for_org(self, org_id: str) -> None:
        """Stop all scheduled tasks for an organization."""
        for registry in (self._heartbeat_tasks, self._standup_tasks):
            task = registry.pop(org_id, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def stop_all(self) -> None:
        org_ids = list(set(list(self._heartbeat_tasks.keys()) + list(self._standup_tasks.keys())))
        for oid in org_ids:
            await self.stop_for_org(oid)

    async def trigger_heartbeat(self, org_id: str) -> dict:
        """Manually trigger a heartbeat cycle."""
        org = self._runtime.get_org(org_id)
        if not org:
            return {"error": "Organization not found"}
        return await self._execute_heartbeat(org)

    async def trigger_standup(self, org_id: str) -> dict:
        """Manually trigger a standup meeting."""
        org = self._runtime.get_org(org_id)
        if not org:
            return {"error": "Organization not found"}
        return await self._execute_standup(org)

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self, org: Organization) -> None:
        while True:
            try:
                interval = self._compute_adaptive_interval(org)
                logger.info(f"[Heartbeat] Next heartbeat for {org.name} in {interval:.0f}s")
                await asyncio.sleep(interval)

                current = self._runtime.get_org(org.id)
                if not current:
                    logger.info(f"[Heartbeat] Org {org.id} no longer exists, stopping heartbeat")
                    break
                if current.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                    continue
                if not current.heartbeat_enabled:
                    break

                await self._execute_heartbeat(current)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Heartbeat] Error in heartbeat loop for {org.id}: {e}")
                await asyncio.sleep(60)

    async def _execute_heartbeat(self, org: Organization) -> dict:
        """Execute a single heartbeat cycle."""
        roots = org.get_root_nodes()
        if not roots:
            return {"error": "No root nodes"}
        root = roots[0]

        if root.status == NodeStatus.BUSY:
            logger.debug(f"[Heartbeat] Skipping: root {root.id} is BUSY")
            return {"skipped": True, "reason": "root_busy"}

        running = self._runtime._running_tasks.get(org.id, {})
        root_busy_tasks = {
            k: t for k, t in running.items()
            if k.startswith(f"{root.id}:") and not t.done()
        }
        if root_busy_tasks:
            logger.debug(f"[Heartbeat] Skipping: root {root.id} has {len(root_busy_tasks)} running task(s)")
            return {"skipped": True, "reason": "root_has_task"}

        await self._recover_error_nodes(org)

        es = self._runtime.get_event_store(org.id)
        bb = self._runtime.get_blackboard(org.id)

        node_summaries = []
        for n in org.nodes:
            messenger = self._runtime.get_messenger(org.id)
            pending = messenger.get_pending_count(n.id) if messenger else 0
            node_summaries.append(
                f"- {n.role_title}({n.department}): status={n.status.value}, pending_messages={pending}"
            )

        blackboard_summary = bb.get_org_summary() if bb else ""

        root_node = roots[0]
        has_external = bool(root_node.external_tools)
        mode = getattr(org, "operation_mode", "command") or "command"

        nl = "\n"

        if mode == "command":
            action_guidance = (
                "## Please follow these steps\n\n"
                "1. **Health check**: Review node statuses for any ERROR or blockage that needs attention\n"
                "2. **Progress review**: Check the blackboard (org_read_blackboard) for project progress and pending items\n"
                "3. **Brief report**: Write current project progress and health status to the blackboard for the manager to review\n"
                "4. **Await instructions**: This organization is in command mode — do not start new tasks proactively; await manager instructions\n\n"
                "If everything is normal, briefly describe the current status."
            )
            review_intro = (
                f"[Health Check] Current time: {_now_iso()}\n\n"
                f"Organization: {org.name}\n\n"
                f"This is a periodic health check. Please review project progress and node health:\n"
            )
        else:
            action_guidance = (
                "## Please follow these steps\n\n"
                "1. **Review**: Check current goals and progress on the blackboard (org_read_blackboard)\n"
                "2. **Assess**: Are all nodes healthy? Any blockages needing intervention?\n"
                "3. **Decide**: Should you start new tasks, adjust priorities, or assign research work?\n"
            )
            if has_external:
                action_guidance += (
                    "4. **Execute**: Use org_delegate_task to assign tasks to subordinates, "
                    "or use create_plan to make a plan, web_search to search for information\n"
                )
            else:
                action_guidance += (
                    "4. **Execute**: Use org_delegate_task to assign tasks, org_broadcast to post announcements\n"
                )
            action_guidance += (
                "5. **Record**: Write decisions and next steps to the blackboard (org_write_blackboard)\n\n"
                "If everything is normal and no new actions are needed, briefly describe the current status."
            )

            persona_label = org.user_persona.label if org.user_persona else "User"
            biz_section = ""
            if org.core_business:
                biz_section = f"## Core Business Objectives\n{org.core_business}\n\n"
            if org.core_business:
                review_intro = (
                    f"[Operations Review] Current time: {_now_iso()}\n\n"
                    f"Organization: {org.name}\n\n"
                    f"{biz_section}"
                    f"This is a periodic operations review. Please review progress and advance next-phase work:\n"
                    f"1. First check the blackboard (org_read_blackboard) for previous decisions and progress\n"
                    f"2. Assess node execution status and identify blockages and deviations\n"
                    f"3. Adjust strategy, assign new tasks, and advance unfinished work\n"
                    f"4. Write this review's conclusions and next-step plans to the blackboard\n\n"
                )
            else:
                review_intro = (
                    f"[Heartbeat Check] Current time: {_now_iso()}\n\n"
                    f"Organization: {org.name}\n"
                    f"Heartbeat prompt: {org.heartbeat_prompt}\n\n"
                )

        persona_label = org.user_persona.label if org.user_persona else "User"

        prompt = (
            f"{review_intro}"
            f"## Node Statuses\n{nl.join(node_summaries)}\n\n"
            f"## Organization Blackboard Summary\n{blackboard_summary}\n\n"
            f"{action_guidance}\n\n"
            f"Note: This heartbeat cascade depth is limited to {org.heartbeat_max_cascade_depth} levels. "
            f"Please control delegation depth carefully.\n"
            f"Important decisions and progress should be written to the blackboard proactively so that {persona_label} can stay informed when reviewing organization status."
        )

        es.emit("heartbeat_triggered", "system", {
            "node_count": len(org.nodes),
        })
        await self._runtime._broadcast_ws("org:heartbeat_start", {
            "org_id": org.id, "type": "heartbeat",
            "has_core_business": bool(org.core_business),
        })

        result = await self._runtime.send_command(org.id, roots[0].id, prompt)

        es.emit("heartbeat_decision", roots[0].id, {
            "result_preview": str(result.get("result", ""))[:200],
        })
        await self._runtime._broadcast_ws("org:heartbeat_done", {
            "org_id": org.id, "type": "heartbeat",
            "result_preview": str(result.get("result", ""))[:120],
        })

        self._tasks_since_review[org.id] = 0

        dismissed = await self._runtime.get_scaler().try_reclaim_idle_clones(org.id)
        if dismissed:
            es.emit("clones_reclaimed", "system", {"dismissed": dismissed})
            logger.info(f"[Heartbeat] Reclaimed {len(dismissed)} idle clones")

        return result

    # ------------------------------------------------------------------
    # Standup loop
    # ------------------------------------------------------------------

    @staticmethod
    def _cron_matches_now(cron_expr: str) -> bool:
        """Check if a 5-field cron expression matches the current minute (UTC)."""
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return False
        now = datetime.now(UTC)
        fields = [now.minute, now.hour, now.day, now.month, now.isoweekday() % 7]
        ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
        for part, val, (lo, hi) in zip(parts, fields, ranges, strict=False):
            if part == "*":
                continue
            try:
                allowed: set[int] = set()
                for segment in part.split(","):
                    if "/" in segment:
                        base, step_s = segment.split("/", 1)
                        step = int(step_s)
                        start = lo if base == "*" else int(base)
                        allowed.update(range(start, hi + 1, step))
                    elif "-" in segment:
                        a, b = segment.split("-", 1)
                        allowed.update(range(int(a), int(b) + 1))
                    else:
                        allowed.add(int(segment))
                if val not in allowed:
                    return False
            except (ValueError, TypeError):
                return False
        return True

    async def _standup_loop(self, org: Organization) -> None:
        """Hybrid standup: triggers on cron schedule OR milestone (N tasks done / all idle)."""
        milestone_threshold = 5
        last_cron_trigger_minute: str = ""
        while True:
            try:
                await asyncio.sleep(60)

                current = self._runtime.get_org(org.id)
                if not current:
                    logger.info(f"[Heartbeat] Org {org.id} no longer exists, stopping standup")
                    break
                if current.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                    continue
                if not current.standup_enabled:
                    break

                tasks_done = self._tasks_since_review.get(org.id, 0)
                all_idle = all(
                    n.status.value == "idle" for n in current.nodes if not n.is_clone
                )

                milestone_trigger = (
                    tasks_done >= milestone_threshold
                    or (all_idle and tasks_done > 0)
                )

                now_key = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
                cron_trigger = False
                cron_expr = getattr(current, "standup_cron", "") or ""
                if cron_expr and now_key != last_cron_trigger_minute:
                    if self._cron_matches_now(cron_expr):
                        cron_trigger = True
                        last_cron_trigger_minute = now_key

                if milestone_trigger or cron_trigger:
                    reason = "cron" if cron_trigger else "milestone"
                    logger.info(
                        f"[Heartbeat] Standup review for {org.id} ({reason}): "
                        f"{tasks_done} tasks done, all_idle={all_idle}"
                    )
                    await self._execute_standup(current)
                    self._tasks_since_review[org.id] = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Heartbeat] Standup error for {org.id}: {e}")
                await asyncio.sleep(60)

    async def _execute_standup(self, org: Organization) -> dict:
        """Execute a standup meeting."""
        roots = org.get_root_nodes()
        if not roots:
            return {"error": "No root nodes"}

        es = self._runtime.get_event_store(org.id)
        self._runtime.get_blackboard(org.id)

        bb = self._runtime.get_blackboard(org.id)
        node_reports = []
        for n in org.nodes:
            if n.id == roots[0].id:
                continue
            parts_detail: list[str] = []
            try:
                recent_events = es.query(actor=n.id, limit=5)
                if recent_events:
                    evt_parts = []
                    for evt in recent_events:
                        etype = evt.get("event_type", "")
                        data = evt.get("data", {})
                        detail = (
                            data.get("task", "")
                            or data.get("content", "")
                            or data.get("summary", "")
                            or data.get("name", "")
                        )
                        if detail:
                            evt_parts.append(f"{etype}: {detail[:50]}")
                        else:
                            evt_parts.append(etype)
                    parts_detail.append("Events: " + "; ".join(evt_parts))
            except Exception:
                pass
            try:
                node_entries = bb.read_node(n.id, limit=3)
                if node_entries:
                    for pe in node_entries:
                        content = pe.content if hasattr(pe, "content") else str(pe)
                        if content:
                            parts_detail.append(f"Work log: {content[:80]}")
            except Exception:
                pass
            messenger = self._runtime.get_messenger(org.id)
            pending = messenger.get_pending_count(n.id) if messenger else 0
            line = f"- {n.role_title}({n.department}): status={n.status.value}, pending={pending}"
            if parts_detail:
                line += "\n    " + "\n    ".join(parts_detail)
            node_reports.append(line)

        nl = "\n"
        prompt = (
            f"[Standup] Current time: {_now_iso()}\n\n"
            f"Organization: {org.name}\n"
            f"Standup agenda: {org.standup_agenda}\n\n"
            f"## Team Member Status\n{nl.join(node_reports)}\n\n"
            f"Please run today's standup meeting:\n"
            f"1. Review each node's progress\n"
            f"2. Identify blockages and issues\n"
            f"3. Reallocate resources if needed\n"
            f"4. Generate a brief standup summary\n\n"
            f"Write key conclusions to the organization blackboard (org_write_blackboard)."
        )

        es.emit("standup_started", "system")
        await self._runtime._broadcast_ws("org:heartbeat_start", {
            "org_id": org.id, "type": "standup",
        })
        result = await self._runtime.send_command(org.id, roots[0].id, prompt)
        es.emit("standup_completed", "system", {
            "result_preview": str(result.get("result", ""))[:200],
        })
        await self._runtime._broadcast_ws("org:heartbeat_done", {
            "org_id": org.id, "type": "standup",
            "result_preview": str(result.get("result", ""))[:120],
        })

        now = datetime.now(UTC)
        report_path = self._runtime._manager._org_dir(org.id) / "reports" / f"standup_{now.strftime('%Y-%m-%d')}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_content = (
            f"# Standup Summary {now.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"**Organization**: {org.name}\n\n"
            f"## Conclusions\n{result.get('result', 'None')}\n"
        )
        await asyncio.to_thread(report_path.write_text, report_content, encoding="utf-8")

        return result
