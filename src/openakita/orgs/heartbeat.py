"""
OrgHeartbeat — 心跳调度、晨会/周报生成

定期触发顶层 Agent 审视组织状态，支持晨会和周报自动生成。
通过 heartbeat_max_cascade_depth 限制级联 LLM 调用深度。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import OrgRuntime

from .models import Organization, OrgStatus, NodeStatus, _now_iso

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
                if not current or current.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
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
        if root.id in running and not running[root.id].done():
            logger.debug(f"[Heartbeat] Skipping: root {root.id} has running task")
            return {"skipped": True, "reason": "root_has_task"}

        await self._recover_error_nodes(org)

        es = self._runtime.get_event_store(org.id)
        bb = self._runtime.get_blackboard(org.id)

        node_summaries = []
        for n in org.nodes:
            messenger = self._runtime.get_messenger(org.id)
            pending = messenger.get_pending_count(n.id) if messenger else 0
            node_summaries.append(
                f"- {n.role_title}({n.department}): 状态={n.status.value}, 待处理消息={pending}"
            )

        blackboard_summary = bb.get_org_summary() if bb else ""

        root_node = roots[0]
        has_external = bool(root_node.external_tools)
        mode = getattr(org, "operation_mode", "command") or "command"

        nl = "\n"

        if mode == "command":
            action_guidance = (
                "## 请按以下步骤思考和行动\n\n"
                "1. **健康检查**：查看各节点状态，是否有 ERROR 或阻塞需要关注\n"
                "2. **进度回顾**：查看黑板（org_read_blackboard）了解项目进展和待办\n"
                "3. **简要汇报**：将当前项目进度和健康状况写入黑板，供负责人查阅\n"
                "4. **等待指令**：本组织为指令模式，不主动启动新任务，等待负责人下达指令\n\n"
                "如果一切正常，简要说明当前状态即可。"
            )
            review_intro = (
                f"[健康检查] 当前时间: {_now_iso()}\n\n"
                f"组织: {org.name}\n\n"
                f"这是定期健康检查，请关注项目进度和节点健康状况：\n"
            )
        else:
            action_guidance = (
                "## 请按以下步骤思考和行动\n\n"
                "1. **回顾**：查看黑板上的当前目标和进展（org_read_blackboard）\n"
                "2. **评估**：各节点状态是否正常？有无阻塞需要干预？\n"
                "3. **决策**：是否需要启动新任务、调整优先级、或分配调研工作？\n"
            )
            if has_external:
                action_guidance += (
                    "4. **执行**：使用 org_delegate_task 分配任务给下属，"
                    "或自己使用 create_todo 制定计划、web_search 搜索信息\n"
                )
            else:
                action_guidance += (
                    "4. **执行**：使用 org_delegate_task 分配任务，org_broadcast 发布公告\n"
                )
            action_guidance += (
                "5. **记录**：将决策和下一步行动写入黑板（org_write_blackboard）\n\n"
                "如果一切正常且无需新行动，简要说明当前状态即可。"
            )

            persona_label = org.user_persona.label if org.user_persona else "用户"
            biz_section = ""
            if org.core_business:
                biz_section = f"## 核心业务目标\n{org.core_business}\n\n"
            if org.core_business:
                review_intro = (
                    f"[经营复盘] 当前时间: {_now_iso()}\n\n"
                    f"组织: {org.name}\n\n"
                    f"{biz_section}"
                    f"这是定期经营复盘，请回顾进展并推进下一阶段工作：\n"
                    f"1. 先查看黑板（org_read_blackboard）了解上次的决策和进展\n"
                    f"2. 评估各节点执行情况，识别阻塞和偏差\n"
                    f"3. 调整策略、分配新任务、推进未完成的工作\n"
                    f"4. 将本轮复盘结论和下一步计划写入黑板\n\n"
                )
            else:
                review_intro = (
                    f"[心跳检查] 当前时间: {_now_iso()}\n\n"
                    f"组织: {org.name}\n"
                    f"心跳提示: {org.heartbeat_prompt}\n\n"
                )

        persona_label = org.user_persona.label if org.user_persona else "用户"

        prompt = (
            f"{review_intro}"
            f"## 各节点状态\n{nl.join(node_summaries)}\n\n"
            f"## 组织黑板摘要\n{blackboard_summary}\n\n"
            f"{action_guidance}\n\n"
            f"注意：本次心跳级联深度限制为 {org.heartbeat_max_cascade_depth} 层，"
            f"请谨慎控制委派深度。\n"
            f"重要决策和进展应主动写入黑板，以便{persona_label}在查看组织状态时了解最新情况。"
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

    async def _standup_loop(self, org: Organization) -> None:
        """Milestone-driven review: triggers when N tasks complete or all nodes idle."""
        milestone_threshold = 5
        while True:
            try:
                await asyncio.sleep(60)

                current = self._runtime.get_org(org.id)
                if not current or current.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                    continue
                if not current.standup_enabled:
                    break

                tasks_done = self._tasks_since_review.get(org.id, 0)
                all_idle = all(
                    n.status.value == "idle" for n in current.nodes if not n.is_clone
                )

                should_review = (
                    tasks_done >= milestone_threshold
                    or (all_idle and tasks_done > 0)
                )

                if should_review:
                    logger.info(
                        f"[Heartbeat] Milestone review for {org.id}: "
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
                    parts_detail.append("事件: " + "; ".join(evt_parts))
            except Exception:
                pass
            try:
                node_entries = bb.read_node(n.id, limit=3)
                if node_entries:
                    for pe in node_entries:
                        content = pe.content if hasattr(pe, "content") else str(pe)
                        if content:
                            parts_detail.append(f"工作记录: {content[:80]}")
            except Exception:
                pass
            messenger = self._runtime.get_messenger(org.id)
            pending = messenger.get_pending_count(n.id) if messenger else 0
            line = f"- {n.role_title}({n.department}): 状态={n.status.value}, 待处理={pending}"
            if parts_detail:
                line += "\n    " + "\n    ".join(parts_detail)
            node_reports.append(line)

        nl = "\n"
        prompt = (
            f"[晨会] 当前时间: {_now_iso()}\n\n"
            f"组织: {org.name}\n"
            f"晨会议程: {org.standup_agenda}\n\n"
            f"## 团队成员状态\n{nl.join(node_reports)}\n\n"
            f"请主持今日晨会：\n"
            f"1. 点评各节点进展\n"
            f"2. 识别阻塞和问题\n"
            f"3. 调配资源（如需要）\n"
            f"4. 生成简要晨会纪要\n\n"
            f"将关键结论写入组织黑板（org_write_blackboard）。"
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

        now = datetime.now(timezone.utc)
        report_path = self._runtime._manager._org_dir(org.id) / "reports" / f"standup_{now.strftime('%Y-%m-%d')}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_content = (
            f"# 晨会纪要 {now.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"**组织**: {org.name}\n\n"
            f"## 结论\n{result.get('result', '无')}\n"
        )
        await asyncio.to_thread(report_path.write_text, report_content, encoding="utf-8")

        return result
