"""
OrgRuntime — 组织运行时引擎

负责组织生命周期管理、节点 Agent 按需激活、
任务调度、消息分发、WebSocket 事件广播。
集成心跳、定时任务、扩编、收件箱、通知、制度管理等子系统。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .blackboard import OrgBlackboard
from .event_store import OrgEventStore
from .identity import OrgIdentity
from .messenger import OrgMessenger
from .models import (
    MemoryType,
    MsgType,
    NodeStatus,
    Organization,
    OrgMessage,
    OrgNode,
    OrgStatus,
    _now_iso,
)
from .tool_handler import OrgToolHandler
from .tools import ORG_NODE_TOOLS

if TYPE_CHECKING:
    from .heartbeat import OrgHeartbeat
    from .inbox import OrgInbox
    from .manager import OrgManager
    from .node_scheduler import OrgNodeScheduler
    from .notifier import OrgNotifier
    from .policies import OrgPolicies
    from .reporter import OrgReporter
    from .scaler import OrgScaler

logger = logging.getLogger(__name__)

AGENT_CACHE_MAX = 10
AGENT_CACHE_TTL = 600
_CIRCUIT_BREAKER_THRESHOLD = 3
_ORG_QUOTA_PAUSE_THRESHOLD = 2

_LIM_EVENT = 10000
_LIM_WS = 2000
_LIM_LOG = 500

_runtime_instance: OrgRuntime | None = None


def get_runtime() -> OrgRuntime | None:
    """Return the active OrgRuntime singleton (set during __init__)."""
    return _runtime_instance


class _CachedAgent:
    """Wrapper for a cached Agent instance with TTL tracking."""
    __slots__ = ("agent", "last_used", "session_id")

    def __init__(self, agent: Any, session_id: str):
        self.agent = agent
        self.session_id = session_id
        self.last_used = time.monotonic()

    def touch(self) -> None:
        self.last_used = time.monotonic()

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.last_used) > AGENT_CACHE_TTL


class OrgRuntime:
    """Core runtime engine for organization orchestration."""

    def __init__(self, manager: OrgManager) -> None:
        self._manager = manager
        self._messengers: dict[str, OrgMessenger] = {}
        self._blackboards: dict[str, OrgBlackboard] = {}
        self._event_stores: dict[str, OrgEventStore] = {}
        self._identities: dict[str, OrgIdentity] = {}
        self._policies: dict[str, OrgPolicies] = {}
        self._tool_handler = OrgToolHandler(self)

        from .heartbeat import OrgHeartbeat
        from .inbox import OrgInbox
        from .node_scheduler import OrgNodeScheduler
        from .notifier import OrgNotifier
        from .scaler import OrgScaler

        self._heartbeat = OrgHeartbeat(self)
        self._scheduler = OrgNodeScheduler(self)
        self._scaler = OrgScaler(self)
        self._inbox = OrgInbox(self)
        self._notifier = OrgNotifier(self)

        from .reporter import OrgReporter
        self._reporter = OrgReporter(self)

        self._agent_cache: OrderedDict[str, _CachedAgent] = OrderedDict()

        self._watchdog_tasks: dict[str, asyncio.Task] = {}
        self._node_busy_since: dict[str, float] = {}
        self._node_last_activity: dict[str, float] = {}

        self._running_tasks: dict[str, dict[str, asyncio.Task]] = {}

        self._active_orgs: dict[str, Organization] = {}

        self._chain_delegation_depth: dict[str, int] = {}  # chain_id -> delegation depth
        self._node_current_chain: dict[str, str] = {}  # org_id:node_id -> chain_id
        self.max_concurrent_per_node: int = 2
        self._idle_tasks: dict[str, asyncio.Task] = {}

        # 组织级并发控制：限制每个组织同时激活的节点数
        self.max_concurrent_nodes_per_org: int = 5
        self._org_semaphores: dict[str, asyncio.Semaphore] = {}

        self._save_locks: dict[str, asyncio.Lock] = {}

        self._node_consecutive_failures: dict[str, int] = {}
        self._org_quota_failures: dict[str, int] = {}

        self._post_hook_cooldown: dict[str, float] = {}
        self._suppress_post_hook: dict[str, bool] = {}
        self._latest_root_result: dict[str, dict] = {}

        self._started = False

        global _runtime_instance
        _runtime_instance = self

    def _get_org_semaphore(self, org_id: str) -> asyncio.Semaphore:
        """获取组织级并发信号量（限制同时激活的节点数）。"""
        sem = self._org_semaphores.get(org_id)
        if sem is None:
            sem = asyncio.Semaphore(self.max_concurrent_nodes_per_org)
            self._org_semaphores[org_id] = sem
        return sem

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialize runtime, recover active organizations."""
        if self._started:
            return
        self._started = True
        logger.info("[OrgRuntime] Starting...")

        for info in self._manager.list_orgs(include_archived=False):
            org = self._manager.get(info["id"])
            if org and org.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                self._activate_org(org)
                await self._heartbeat.start_for_org(org)
                await self._scheduler.start_for_org(org)

                await self._recover_pending_tasks(org)
                logger.info(f"[OrgRuntime] Recovered org: {org.name} ({org.status.value})")

        logger.info("[OrgRuntime] Started.")

    async def shutdown(self) -> None:
        """Gracefully shut down all active organizations."""
        logger.info("[OrgRuntime] Shutting down...")

        await self._heartbeat.stop_all()
        await self._scheduler.stop_all()

        for _org_id, idle_task in list(self._idle_tasks.items()):
            if idle_task and not idle_task.done():
                idle_task.cancel()
        self._idle_tasks.clear()

        for _org_id, watchdog_task in list(self._watchdog_tasks.items()):
            if watchdog_task and not watchdog_task.done():
                watchdog_task.cancel()
        self._watchdog_tasks.clear()

        for _org_id, tasks in list(self._running_tasks.items()):
            for _node_id, task in tasks.items():
                if not task.done():
                    task.cancel()
            tasks.clear()
        self._running_tasks.clear()

        for _key, cached in list(self._agent_cache.items()):
            try:
                if hasattr(cached.agent, "shutdown"):
                    await cached.agent.shutdown()
            except Exception:
                pass
        self._agent_cache.clear()

        for org_id in list(self._active_orgs.keys()):
            self._save_state(org_id)
            messenger = self._messengers.get(org_id)
            if messenger:
                await messenger.stop_background_tasks()

        self._active_orgs.clear()
        self._messengers.clear()
        self._blackboards.clear()
        self._event_stores.clear()
        self._identities.clear()
        self._policies.clear()
        self._org_semaphores.clear()
        self._save_locks.clear()
        self._node_busy_since.clear()
        self._node_last_activity.clear()
        self._node_current_chain.clear()
        self._chain_delegation_depth.clear()
        self._org_quota_failures.clear()

        self._started = False
        logger.info("[OrgRuntime] Shutdown complete.")

    # ------------------------------------------------------------------
    # Lifecycle state machine
    # ------------------------------------------------------------------

    _VALID_TRANSITIONS: dict[OrgStatus, set[OrgStatus]] = {
        OrgStatus.DORMANT: {OrgStatus.ACTIVE},
        OrgStatus.ACTIVE: {OrgStatus.RUNNING, OrgStatus.PAUSED, OrgStatus.DORMANT, OrgStatus.ARCHIVED},
        OrgStatus.RUNNING: {OrgStatus.ACTIVE, OrgStatus.PAUSED, OrgStatus.DORMANT},
        OrgStatus.PAUSED: {OrgStatus.ACTIVE, OrgStatus.DORMANT, OrgStatus.ARCHIVED},
        OrgStatus.ARCHIVED: set(),
    }

    def _check_transition(self, org: Organization, target: OrgStatus) -> None:
        valid = self._VALID_TRANSITIONS.get(org.status, set())
        if target not in valid:
            raise ValueError(
                f"无效状态转换: {org.status.value} -> {target.value} "
                f"(允许的目标: {', '.join(s.value for s in valid) or '无'})"
            )

    # ------------------------------------------------------------------
    # Organization lifecycle
    # ------------------------------------------------------------------

    async def start_org(self, org_id: str) -> Organization:
        """Start an organization, transitioning it to ACTIVE."""
        org = self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        self._check_transition(org, OrgStatus.ACTIVE)

        prev_status = org.status
        org.status = OrgStatus.ACTIVE
        org.updated_at = _now_iso()
        self._manager.update(org_id, {"status": org.status.value})

        try:
            self._activate_org(org)
            await self._recover_pending_tasks(org)
            await self._heartbeat.start_for_org(org)
            await self._scheduler.start_for_org(org)
        except Exception:
            logger.error("[OrgRuntime] start_org failed, rolling back", exc_info=True)
            org.status = prev_status
            self._manager.update(org_id, {"status": prev_status.value})
            try:
                await self._stop_org_services(org_id)
                await self._cancel_org_tasks(org_id)
                await self._deactivate_org(org_id)
            except Exception:
                logger.debug("[OrgRuntime] rollback cleanup error", exc_info=True)
            raise

        policies = self.get_policies(org_id)
        if policies:
            try:
                getattr(org, "_source_template", None)
            except Exception:
                pass
            existing = policies.list_policies()
            if not existing:
                policies.install_default_policies("default")

        self.get_event_store(org_id).emit("org_started", "system")
        await self._broadcast_ws("org:status_change", {
            "org_id": org_id, "status": "active"
        })

        mode = getattr(org, "operation_mode", "command") or "command"
        if mode == "autonomous":
            if org.core_business and org.core_business.strip():
                asyncio.ensure_future(self._auto_kickoff(org))
            self._idle_tasks[org_id] = asyncio.ensure_future(self._idle_probe_loop(org_id))
        else:
            self._idle_tasks[org_id] = asyncio.ensure_future(self._health_check_loop(org_id))

        if getattr(org, "watchdog_enabled", False):
            self._watchdog_tasks[org_id] = asyncio.ensure_future(self._watchdog_loop(org_id))

        return org

    async def _stop_org_services(self, org_id: str) -> None:
        """Stop heartbeat and scheduler for an organization."""
        await self._heartbeat.stop_for_org(org_id)
        await self._scheduler.stop_for_org(org_id)

    async def _cancel_org_tasks(self, org_id: str) -> None:
        """Cancel all background tasks (idle, watchdog, running) for an organization."""
        idle_task = self._idle_tasks.pop(org_id, None)
        if idle_task and not idle_task.done():
            idle_task.cancel()

        watchdog_task = self._watchdog_tasks.pop(org_id, None)
        if watchdog_task and not watchdog_task.done():
            watchdog_task.cancel()
            try:
                await watchdog_task
            except (asyncio.CancelledError, Exception):
                pass

        org_tasks = self._running_tasks.pop(org_id, {})
        for _node_id, task in org_tasks.items():
            if not task.done():
                task.cancel()
        for _node_id, task in org_tasks.items():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def stop_org(self, org_id: str) -> Organization:
        """Stop an organization."""
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        self._check_transition(org, OrgStatus.DORMANT)

        await self._stop_org_services(org_id)
        await self._cancel_org_tasks(org_id)

        reset_nodes = []
        for node in org.nodes:
            if node.status in (NodeStatus.BUSY, NodeStatus.WAITING, NodeStatus.ERROR):
                self._set_node_status(org, node, NodeStatus.IDLE, "org_stopped")
                reset_nodes.append(node)

        for node in reset_nodes:
            await self._broadcast_ws("org:node_status", {
                "org_id": org_id, "node_id": node.id,
                "status": "idle", "current_task": None,
            })

        org.status = OrgStatus.DORMANT
        org.updated_at = _now_iso()
        self._manager.update(org_id, {"status": org.status.value})
        await self._save_org(org)

        self.get_event_store(org_id).emit("org_stopped", "system")
        await self._deactivate_org(org_id)

        await self._broadcast_ws("org:status_change", {
            "org_id": org_id, "status": "dormant"
        })

        return org

    async def delete_org(self, org_id: str) -> None:
        """Permanently delete an organization: stop runtime, clean all state, remove disk data."""
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        # 1. Graceful stop (best-effort)
        if org.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING, OrgStatus.PAUSED):
            try:
                await self.stop_org(org_id)
            except Exception as e:
                logger.warning(f"[OrgRuntime] stop_org before delete failed for {org_id}: {e}")

        # 2. Force-stop all background tasks regardless of stop_org result.
        #    Each call is idempotent — safe even if stop_org already cleaned them.
        try:
            await self._heartbeat.stop_for_org(org_id)
        except Exception:
            pass
        try:
            await self._scheduler.stop_for_org(org_id)
        except Exception:
            pass

        org_tasks = self._running_tasks.pop(org_id, {})
        for task in org_tasks.values():
            if not task.done():
                task.cancel()

        idle_task = self._idle_tasks.pop(org_id, None)
        if idle_task and not idle_task.done():
            idle_task.cancel()

        watchdog_task = self._watchdog_tasks.pop(org_id, None)
        if watchdog_task and not watchdog_task.done():
            watchdog_task.cancel()

        # 3. Remove in-memory references
        await self._deactivate_org(org_id)
        self._org_semaphores.pop(org_id, None)
        self._save_locks.pop(org_id, None)

        # 4. Delete disk data
        self._manager.delete(org_id)

        await self._broadcast_ws("org:status_change", {
            "org_id": org_id, "status": "deleted"
        })
        logger.info(f"[OrgRuntime] Deleted org: {org_id} ({org.name})")

    async def reset_org(self, org_id: str) -> Organization:
        """Reset an organization: stop runtime, clear all data, prepare for fresh start."""
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        # 1. Stop services and cancel tasks (without calling stop_org/_deactivate_org,
        #    so that in-memory references remain alive for data cleanup below)
        await self._stop_org_services(org_id)
        await self._cancel_org_tasks(org_id)

        # 2. Reset all node statuses to idle, clear frozen state and current_task
        for node in org.nodes:
            self._set_node_status(org, node, NodeStatus.IDLE, "org_reset")
            node.frozen_by = None
            node.frozen_reason = None
            node.frozen_at = None
            node.current_task = None

        # 3. Evict all agent caches for this org
        keys_to_evict = [k for k in self._agent_cache if k.startswith(f"{org_id}:")]
        for k in keys_to_evict:
            self._agent_cache.pop(k, None)

        # 4. Clear data stores while references are still alive
        bb = self._blackboards.get(org_id)
        if bb and hasattr(bb, "clear"):
            bb.clear()

        es = self._event_stores.get(org_id)
        if es and hasattr(es, "clear"):
            es.clear()

        messenger = self._messengers.get(org_id)
        if messenger and hasattr(messenger, "clear_all"):
            messenger.clear_all()

        # Emit a single audit event as the first entry in the fresh event store
        if es:
            es.emit("org_reset", "system", {"reason": "org_reset"})

        # 5. Tear down all in-memory references
        await self._deactivate_org(org_id)

        # 6. Save clean state
        org.status = OrgStatus.DORMANT
        org.updated_at = _now_iso()
        self._manager.update(org_id, org.to_dict())

        logger.info(f"[OrgRuntime] Reset org {org.name} ({org_id})")

        await self._broadcast_ws("org:status_change", {
            "org_id": org_id, "status": "dormant",
        })

        return org

    async def pause_org(self, org_id: str) -> Organization:
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")
        self._check_transition(org, OrgStatus.PAUSED)
        org.status = OrgStatus.PAUSED
        org.updated_at = _now_iso()
        self._manager.update(org_id, {"status": org.status.value})
        self.get_event_store(org_id).emit("org_paused", "system")
        await self._broadcast_ws("org:status_change", {
            "org_id": org_id, "status": "paused"
        })
        return org

    async def resume_org(self, org_id: str) -> Organization:
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")
        self._check_transition(org, OrgStatus.ACTIVE)
        org.status = OrgStatus.ACTIVE
        org.updated_at = _now_iso()
        self._manager.update(org_id, {"status": org.status.value})
        self._org_quota_failures.pop(org_id, None)
        if org_id not in self._active_orgs:
            self._activate_org(org)
        self.get_event_store(org_id).emit("org_resumed", "system")
        await self._broadcast_ws("org:status_change", {
            "org_id": org_id, "status": "active"
        })
        return org

    # ------------------------------------------------------------------
    # User commands
    # ------------------------------------------------------------------

    async def send_command(
        self,
        org_id: str,
        target_node_id: str | None,
        content: str,
        *,
        chain_id: str | None = None,
    ) -> dict:
        """Send a user command to an organization node."""
        org = self._active_orgs.get(org_id)
        if not org:
            org = self._manager.get(org_id)
            if not org:
                raise ValueError(f"Organization not found: {org_id}")
            if org.status == OrgStatus.PAUSED:
                org = await self.resume_org(org_id)
            elif org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                org = await self.start_org(org_id)
        elif org.status == OrgStatus.PAUSED:
            org = await self.resume_org(org_id)

        if not target_node_id:
            roots = org.get_root_nodes()
            if not roots:
                raise ValueError("Organization has no root nodes")
            target_node_id = roots[0].id

        target = org.get_node(target_node_id)
        if not target:
            raise ValueError(f"Node not found: {target_node_id}")

        self.get_event_store(org_id).emit(
            "user_command", "user",
            {"target": target_node_id, "content": content[:_LIM_EVENT]},
        )

        persona = org.user_persona
        if persona and persona.label:
            tagged_content = f"[来自 {persona.label}] {content}"
        else:
            tagged_content = content

        self._suppress_post_hook.pop(org_id, None)

        result = await self._activate_and_run(org, target, tagged_content, chain_id=chain_id)

        if self._is_stop_intent(content):
            await self._soft_stop_org(org_id)
            if chain_id and isinstance(result, dict):
                result["chain_id"] = chain_id
            return result

        if self._has_active_delegations(org_id, target.id):
            final = await self._wait_delegation_completion(org_id, target.id, timeout=300)
            if final:
                result = final

        if chain_id and isinstance(result, dict):
            result["chain_id"] = chain_id
        return result

    async def cancel_node_task(
        self,
        org_id: str,
        node_id: str,
        reason: str = "用户取消任务",
    ) -> dict:
        """Cancel the running task on a specific node.

        1. Cancel the Agent's internal TaskState so the ReAct loop stops
        2. Cancel the asyncio.Task wrapper in _running_tasks
        3. Reset node status to IDLE
        4. Broadcast status change events
        """
        org = self._active_orgs.get(org_id)
        if not org:
            return {"ok": False, "error": "Organization not running"}

        node = org.get_node(node_id)
        if not node:
            return {"ok": False, "error": f"Node not found: {node_id}"}

        if node.status != NodeStatus.BUSY:
            return {"ok": False, "error": f"Node {node_id} is not busy (status={node.status.value})"}

        cache_key = f"{org_id}:{node_id}"
        session_id = f"org:{org_id}:node:{node_id}"
        cancelled = False

        # (a) Signal the Agent's ReAct loop to stop via cancel_current_task
        cached = self._agent_cache.get(cache_key)
        if cached and hasattr(cached.agent, "cancel_current_task"):
            try:
                cached.agent.cancel_current_task(reason, session_id=session_id)
                logger.info(f"[OrgRuntime] Sent cancel signal to agent {cache_key}")
            except Exception as e:
                logger.warning(f"[OrgRuntime] Agent cancel_current_task failed: {e}")

        # (b) Cancel the asyncio.Task so CancelledError propagates
        org_tasks = self._running_tasks.get(org_id, {})
        for task_key, task in list(org_tasks.items()):
            if task_key.startswith(f"{node_id}:") and not task.done():
                task.cancel()
                cancelled = True
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                org_tasks.pop(task_key, None)
                logger.info(f"[OrgRuntime] Cancelled asyncio task {task_key}")

        # (c) Reset node status
        try:
            self._set_node_status(org, node, NodeStatus.IDLE, f"task_cancelled: {reason}")
            self._node_current_chain.pop(cache_key, None)
            await self._save_org(org)
        except Exception as e:
            logger.warning(f"[OrgRuntime] Failed to reset node status: {e}")

        # (d) Broadcast events
        self.get_event_store(org_id).emit(
            "task_cancelled", node_id, {"reason": reason[:_LIM_EVENT]},
        )
        await self._broadcast_ws("org:node_status", {
            "org_id": org_id, "node_id": node_id, "status": "idle",
            "current_task": "",
        })
        await self._broadcast_ws("org:task_cancelled", {
            "org_id": org_id, "node_id": node_id, "reason": reason[:_LIM_WS],
        })

        logger.info(f"[OrgRuntime] cancel_node_task completed: org={org_id}, node={node_id}, cancelled={cancelled}")
        return {"ok": True, "node_id": node_id, "cancelled": cancelled}

    async def _auto_kickoff(self, org: Organization) -> None:
        """Auto-activate the root node with a mission briefing when org starts
        with core_business set. This enables continuous autonomous operations."""
        try:
            roots = org.get_root_nodes()
            if not roots:
                return
            root = roots[0]
            persona_label = org.user_persona.label if org.user_persona else "负责人"

            prompt = (
                f"[组织启动 — 经营任务书]\n\n"
                f"你是「{org.name}」的 {root.role_title}，组织刚刚启动。\n"
                f"{persona_label}委托你全权负责以下核心业务：\n\n"
                f"---\n{org.core_business.strip()}\n---\n\n"
                f"## 你现在需要做的\n\n"
                f"1. **制定工作策略**：根据核心业务目标，拟定具体的行动计划和阶段性目标\n"
                f"2. **分解和委派**：将工作拆解为具体任务，用 org_delegate_task 分派给合适的下属\n"
                f"3. **启动执行**：不要等待进一步指令，立即开始推进最优先的工作\n"
                f"4. **记录决策**：将工作策略、任务分工、阶段目标写入黑板（org_write_blackboard）\n\n"
                f"## 工作原则\n\n"
                f"- 你是本组织的最高负责人，应自主判断、持续推进，不需要等{persona_label}下达每一步指令\n"
                f"- {persona_label}的指令是方向性调整和补充，日常工作由你全权决策\n"
                f"- 遇到重大决策或风险时，通过黑板记录，{persona_label}会在查看组织状态时看到\n"
                f"- 定期复盘进度，调整策略，确保持续向目标推进\n\n"
                f"现在开始工作。"
            )

            self.get_event_store(org.id).emit(
                "auto_kickoff", "system",
                {"root_node": root.id, "core_business_len": len(org.core_business)},
            )

            await self._activate_and_run(org, root, prompt)
        except Exception as e:
            logger.error(f"[OrgRuntime] Auto-kickoff failed for {org.id}: {e}")

    # ------------------------------------------------------------------
    # Node activation
    # ------------------------------------------------------------------

    def get_current_chain_id(self, org_id: str, node_id: str) -> str | None:
        """Get the current task chain_id for a node (set when processing a message)."""
        return self._node_current_chain.get(f"{org_id}:{node_id}")

    def set_current_chain_id(self, org_id: str, node_id: str, chain_id: str | None) -> None:
        """Set the current task chain_id for a node."""
        key = f"{org_id}:{node_id}"
        if chain_id:
            self._node_current_chain[key] = chain_id
        else:
            self._node_current_chain.pop(key, None)

    async def _activate_and_run(
        self, org: Organization, node: OrgNode, prompt: str,
        chain_id: str | None = None,
    ) -> dict:
        """Activate a node agent and run a task (with org-level concurrency limit)."""
        if node.status == NodeStatus.FROZEN:
            return {"error": f"{node.role_title} 已被冻结，无法执行任务"}
        if node.status == NodeStatus.OFFLINE:
            return {"error": f"{node.role_title} 已下线"}

        sem = self._get_org_semaphore(org.id)
        async with sem:
            return await self._activate_and_run_inner(org, node, prompt, chain_id)

    async def _activate_and_run_inner(
        self, org: Organization, node: OrgNode, prompt: str,
        chain_id: str | None = None,
    ) -> dict:
        """_activate_and_run 的内部实现（已在 org semaphore 保护下）。"""
        if node.status == NodeStatus.FROZEN:
            return {"error": f"{node.role_title} 已被冻结，无法执行任务"}
        if node.status == NodeStatus.OFFLINE:
            return {"error": f"{node.role_title} 已下线"}

        cache_key = f"{org.id}:{node.id}"

        if node.status == NodeStatus.ERROR:
            self._agent_cache.pop(cache_key, None)
            self._set_node_status(org, node, NodeStatus.IDLE, "auto_recover_before_activate")

        agent = await self._get_or_create_agent(org, node)

        self.set_current_chain_id(org.id, node.id, chain_id)
        if hasattr(agent, "_org_context"):
            agent._org_context["current_chain_id"] = chain_id or ""

        self._set_node_status(org, node, NodeStatus.BUSY, "task_started")
        self._node_last_activity[cache_key] = time.monotonic()
        await self._save_org(org)

        if org.id not in self._active_orgs:
            return {"node_id": node.id, "error": "org deleted during activation"}

        self.get_event_store(org.id).emit(
            "node_activated", node.id, {"prompt": prompt[:_LIM_EVENT]},
        )
        await self._broadcast_ws("org:node_status", {
            "org_id": org.id, "node_id": node.id, "status": "busy",
            "current_task": prompt[:_LIM_WS],
        })

        try:
            session_id = f"org:{org.id}:node:{node.id}"

            if hasattr(agent, "brain") and hasattr(agent.brain, "drain_usage_accumulator"):
                agent.brain.drain_usage_accumulator()

            result_text = await self._run_agent_task(
                agent, prompt, session_id, org, node,
            )

            if org.id not in self._active_orgs:
                return {"node_id": node.id, "result": result_text}

            self._set_node_status(org, node, NodeStatus.IDLE, "task_completed")
            org.total_tasks_completed += 1
            self._node_consecutive_failures.pop(f"{org.id}:{node.id}", None)
            self._org_quota_failures.pop(org.id, None)
            await self._save_org(org)
            self._heartbeat.record_activity(org.id)

            if org.id not in self._active_orgs:
                return {"node_id": node.id, "result": result_text}

            self.get_event_store(org.id).emit(
                "task_completed", node.id,
                {"result_preview": result_text[:_LIM_EVENT] if result_text else ""},
            )
            await self._broadcast_ws("org:node_status", {
                "org_id": org.id, "node_id": node.id, "status": "idle",
                "current_task": "",
            })
            await self._broadcast_ws("org:task_complete", {
                "org_id": org.id, "node_id": node.id,
                "result_preview": result_text[:_LIM_WS] if result_text else "",
            })

            is_root = (node.level == 0 or not org.get_parent(node.id))
            if is_root:
                self._latest_root_result[org.id] = {"node_id": node.id, "result": result_text}

            asyncio.ensure_future(self._post_task_hook(org, node))

            return {"node_id": node.id, "result": result_text}

        except Exception as e:
            logger.error(f"[OrgRuntime] Task error on {node.id}: {e}")

            # org-level quota/auth failure detection
            is_quota_auth = self._is_quota_auth_error(e)
            if is_quota_auth:
                count = self._org_quota_failures.get(org.id, 0) + 1
                self._org_quota_failures[org.id] = count
                if count >= _ORG_QUOTA_PAUSE_THRESHOLD:
                    did_pause = await self._pause_org_for_quota(org, e)
                    if did_pause:
                        return {"node_id": node.id, "error": str(e)}

            fail_key = f"{org.id}:{node.id}"
            self._node_consecutive_failures[fail_key] = (
                self._node_consecutive_failures.get(fail_key, 0) + 1
            )
            if self._node_consecutive_failures[fail_key] >= _CIRCUIT_BREAKER_THRESHOLD:
                logger.warning(
                    f"[OrgRuntime] Circuit breaker: {node.role_title} ({node.id}) "
                    f"failed {self._node_consecutive_failures[fail_key]} times, auto-freezing"
                )
                try:
                    node.status = NodeStatus.FROZEN
                    node.frozen_by = "circuit_breaker"
                    node.frozen_reason = (
                        f"连续失败 {self._node_consecutive_failures[fail_key]} 次，自动冻结"
                    )
                    self._set_node_status(org, node, NodeStatus.FROZEN, node.frozen_reason)
                except Exception:
                    node.status = NodeStatus.FROZEN
            else:
                try:
                    self._set_node_status(org, node, NodeStatus.ERROR, str(e)[:_LIM_LOG])
                except Exception:
                    node.status = NodeStatus.ERROR
            try:
                await self._save_org(org)
            except Exception as save_err:
                logger.warning(f"[OrgRuntime] Failed to save error state for {node.id}: {save_err}")
            try:
                es = self.get_event_store(org.id)
                if es:
                    es.emit("task_failed", node.id, {"error": str(e)[:_LIM_EVENT]})
            except Exception:
                pass
            try:
                await self._broadcast_ws("org:node_status", {
                    "org_id": org.id, "node_id": node.id,
                    "status": "frozen" if node.status == NodeStatus.FROZEN else "error",
                    "current_task": "",
                })
            except Exception:
                pass
            return {"node_id": node.id, "error": str(e)}

        finally:
            self._emit_llm_usage(agent, org, node)

    @staticmethod
    def _is_quota_auth_error(error: Exception) -> bool:
        """Check if exception is caused by API quota exhaustion or auth failure."""
        from openakita.llm.types import AllEndpointsFailedError

        if isinstance(error, AllEndpointsFailedError):
            return bool(error.error_categories & {"quota", "auth"})
        err_lower = str(error).lower()
        return any(kw in err_lower for kw in [
            "insufficient balance", "insufficient_balance", "quota",
            "billing", "(402)", "payment required",
        ])

    async def _pause_org_for_quota(self, org: Organization, error: Exception) -> bool:
        """Pause organization due to API quota/auth exhaustion across all endpoints.

        Returns:
            True if this call newly paused the org (caller may short-circuit).
            False if org was already paused, status disallows pause, or pause failed —
            caller should still update the failing node's status.
        """
        if org.status == OrgStatus.PAUSED:
            self._org_quota_failures.pop(org.id, None)
            return False
        if org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
            logger.info(
                f"[OrgRuntime] Quota pause skipped — org {org.id} status={org.status.value}"
            )
            self._org_quota_failures.pop(org.id, None)
            return False

        logger.warning(
            f"[OrgRuntime] Quota/auth failure threshold reached for org {org.name} "
            f"({org.id}), auto-pausing. Error: {str(error)[:_LIM_LOG]}"
        )
        try:
            try:
                self._check_transition(org, OrgStatus.PAUSED)
            except ValueError as ve:
                logger.warning(f"[OrgRuntime] Quota pause: invalid transition: {ve}")
                self._org_quota_failures.pop(org.id, None)
                return False

            nodes_reset: list[str] = []
            for node in org.nodes:
                if node.status in (NodeStatus.BUSY, NodeStatus.WAITING, NodeStatus.ERROR):
                    self._set_node_status(org, node, NodeStatus.IDLE, "org_quota_pause")
                    nodes_reset.append(node.id)

            org.status = OrgStatus.PAUSED
            org.updated_at = _now_iso()
            self._manager.update(org.id, {"status": org.status.value})
            await self._save_org(org)
            self._org_quota_failures.pop(org.id, None)

            self.get_event_store(org.id).emit(
                "org_paused", "system",
                {"reason": "quota_exhausted", "error": str(error)[:_LIM_EVENT]},
            )

            inbox = self.get_inbox(org.id)
            inbox.push_warning(
                org.id, "system",
                title="API 余额不足，组织已自动暂停",
                body=(
                    "所有已配置的 AI 模型端点均因余额不足或认证失败而无法使用。"
                    "组织已自动暂停以避免持续失败。"
                    "请前往对应平台充值后，在组织面板点击「恢复」继续运行。"
                ),
            )

            for nid in nodes_reset:
                try:
                    await self._broadcast_ws("org:node_status", {
                        "org_id": org.id, "node_id": nid, "status": "idle",
                        "current_task": "",
                    })
                except Exception:
                    pass

            await self._broadcast_ws("org:status_change", {
                "org_id": org.id, "status": "paused",
            })
            await self._broadcast_ws("org:quota_exhausted", {
                "org_id": org.id,
                "message": "API 余额不足，组织已自动暂停。请充值后恢复。",
            })
            return True
        except Exception as pause_err:
            logger.error(f"[OrgRuntime] Failed to pause org for quota: {pause_err}")
            return False

    async def _run_agent_task(
        self, agent: Any, prompt: str, session_id: str,
        org: Organization, node: OrgNode,
    ) -> str:
        """Run a single agent task (no timeout wrapper)."""
        from openakita.core.errors import UserCancelledError

        try:
            response = await agent.chat(prompt, session_id=session_id)
            return response or ""
        except (asyncio.CancelledError, UserCancelledError) as cancel_err:
            logger.info(f"[OrgRuntime] Task cancelled for {node.id}: {type(cancel_err).__name__}")
            chain_id = self.get_current_chain_id(org.id, node.id)
            if chain_id:
                try:
                    from openakita.orgs.models import TaskStatus
                    from openakita.orgs.project_store import ProjectStore
                    store = ProjectStore(self._manager._org_dir(org.id))
                    task = store.find_task_by_chain(chain_id)
                    if task and task.status == TaskStatus.IN_PROGRESS:
                        store.update_task(task.project_id, task.id, {
                            "status": TaskStatus.CANCELLED,
                        })
                        logger.info(f"[OrgRuntime] Marked project task {task.id} as cancelled")
                except Exception as e:
                    logger.debug(f"[OrgRuntime] Failed to update task status on cancel: {e}")
            return "(任务已取消)"
        except Exception as e:
            logger.error(f"[OrgRuntime] Agent task error: {e}")
            raise

    def _emit_llm_usage(self, agent: Any, org: Organization, node: OrgNode) -> None:
        """Record per-node LLM usage event after a task completes."""
        try:
            if not (hasattr(agent, "brain") and hasattr(agent.brain, "drain_usage_accumulator")):
                return
            stats = agent.brain.drain_usage_accumulator()
            if stats["calls"] == 0:
                return
            ep_info = agent.brain.get_current_endpoint_info() if hasattr(agent.brain, "get_current_endpoint_info") else {}
            data = {
                "node_id": node.id,
                "calls": stats["calls"],
                "tokens_in": stats["tokens_in"],
                "tokens_out": stats["tokens_out"],
                "model": ep_info.get("model", ""),
            }
            self.get_event_store(org.id).emit("llm_usage", node.id, data)
            logger.info(
                f"[OrgRuntime] LLM usage for {node.id}: "
                f"calls={stats['calls']}, in={stats['tokens_in']}, out={stats['tokens_out']}"
            )
        except Exception as e:
            logger.debug(f"[OrgRuntime] Failed to emit llm_usage: {e}")

    async def _get_or_create_agent(self, org: Organization, node: OrgNode) -> Any:
        """Get cached agent or create a new one."""
        cache_key = f"{org.id}:{node.id}"

        if cache_key in self._agent_cache:
            cached = self._agent_cache[cache_key]
            if not cached.expired:
                cached.touch()
                self._agent_cache.move_to_end(cache_key)
                return cached.agent

        self._evict_expired_agents()

        agent = await self._create_node_agent(org, node)

        session_id = f"org:{org.id}:node:{node.id}"
        self._agent_cache[cache_key] = _CachedAgent(agent, session_id)

        if len(self._agent_cache) > AGENT_CACHE_MAX:
            oldest_key, oldest = self._agent_cache.popitem(last=False)
            logger.debug(f"[OrgRuntime] Evicted agent cache: {oldest_key}")

        return agent

    async def _create_node_agent(self, org: Organization, node: OrgNode) -> Any:
        """Create a new Agent instance for a node."""
        from openakita.agents.factory import AgentFactory

        factory = AgentFactory()

        identity = self._get_identity(org.id)
        resolved = identity.resolve(node, org)

        bb = self.get_blackboard(org.id)
        blackboard_summary = bb.get_org_summary() if bb else ""
        dept_summary = bb.get_dept_summary(node.department) if bb and node.department else ""
        memory_owner = node.clone_source if node.is_clone and node.clone_source else node.id
        node_summary = bb.get_node_summary(memory_owner) if bb else ""

        org_context_prompt = identity.build_org_context_prompt(
            node, org, resolved,
            blackboard_summary=blackboard_summary,
            dept_summary=dept_summary,
            node_summary=node_summary,
        )

        profile = self._build_profile_for_node(node, org_context_prompt)

        agent = await factory.create(profile)

        from .tool_categories import expand_tool_categories

        _KEEP = frozenset({
            "get_tool_info",
            "create_plan",
            "update_plan_step",
            "get_plan_status",
            "complete_plan",
        })

        # Free-form delegation tools conflict with org_delegate_task
        _ORG_CONFLICT_TOOLS = frozenset({
            "delegate_to_agent", "spawn_agent",
            "delegate_parallel", "create_agent",
        })

        allowed_external = expand_tool_categories(node.external_tools) - _ORG_CONFLICT_TOOLS

        if hasattr(agent, "tool_catalog"):
            for tool_def in ORG_NODE_TOOLS:
                agent.tool_catalog.add_tool(tool_def)
            non_org = [
                n for n in agent.tool_catalog.list_tools()
                if not n.startswith("org_") and n not in _KEEP
                and n not in allowed_external
            ]
            for n in non_org:
                agent.tool_catalog.remove_tool(n)

        if hasattr(agent, "_tools"):
            seen: set[str] = set()
            filtered: list[dict] = []
            for t in agent._tools:
                name = t.get("name", "")
                if (name.startswith("org_") or name in _KEEP
                        or name in allowed_external) and name not in seen:
                    seen.add(name)
                    filtered.append(t)
            for t in ORG_NODE_TOOLS:
                name = t["name"]
                if name not in seen:
                    seen.add(name)
                    filtered.append(t)
            agent._tools = filtered

        _MCP_TOOL_NAMES = {"call_mcp_tool", "list_mcp_servers", "get_mcp_instructions"}
        if node.mcp_servers and (
            "mcp" in (node.external_tools or []) or _MCP_TOOL_NAMES & allowed_external
        ):
            self._connect_node_mcp_servers(agent, node.mcp_servers)

        org_workspace = self._resolve_org_workspace(org)
        agent.file_tool.base_path = org_workspace
        agent.shell_tool.default_cwd = str(org_workspace)

        is_root = (node.level == 0 or not org.get_parent(node.id))
        self._override_system_prompt_for_org(agent, org_context_prompt, org_workspace, is_root=is_root)

        agent._org_context = {
            "org_id": org.id,
            "node_id": node.id,
            "tool_handler": self._tool_handler,
            "workspace": org_workspace,
        }

        if hasattr(agent, "brain") and hasattr(agent.brain, "set_trace_context"):
            agent.brain.set_trace_context({
                "org_id": org.id,
                "org_name": org.name,
                "node_id": node.id,
                "node_title": node.role_title,
                "session_id": f"org:{org.id}:node:{node.id}",
            })

        if hasattr(agent, "reasoning_engine"):
            from ..config import settings as _settings
            agent.reasoning_engine._force_tool_override = max(
                1, int(getattr(_settings, "force_tool_call_max_retries", 2))
            )

        self._register_org_tool_handler(agent, org.id, node.id)

        return agent

    def _resolve_org_workspace(self, org: Organization) -> Path:
        """Return the effective workspace directory for an organization.

        Priority: user-configured path > default ``<org_dir>/workspace``.
        """
        custom = (org.workspace_dir or "").strip()
        if custom:
            p = Path(custom)
            if p.is_absolute() and (p.is_dir() or not p.exists()):
                p.mkdir(parents=True, exist_ok=True)
                return p
            logger.warning(
                "[OrgRuntime] workspace_dir %r invalid, falling back to default", custom,
            )
        default = self._manager._org_dir(org.id) / "workspace"
        default.mkdir(parents=True, exist_ok=True)
        return default

    @staticmethod
    def _override_system_prompt_for_org(
        agent: Any, org_context: str, workspace: Path | None = None,
        *, is_root: bool = False,
    ) -> None:
        """Replace the agent's system prompt with an org-focused lean prompt.

        This prompt is used directly by _build_system_prompt_compiled when
        _org_context is set, bypassing the generic prompt pipeline entirely.
        """
        import os
        import platform
        from datetime import datetime

        org_tool_lines: list[str] = []
        ext_tool_lines: list[str] = []

        for t in getattr(agent, "_tools", []):
            name = t.get("name", "")
            desc = t.get("description", "")
            schema = t.get("input_schema", {})
            required = schema.get("required", [])
            props = schema.get("properties", {})
            params = ", ".join(
                f"{p}" + (" *" if p in required else "")
                for p in props
            )
            line = f"- **{name}**({params}): {desc}"
            if name.startswith("org_") or name == "get_tool_info":
                org_tool_lines.append(line)
            else:
                ext_tool_lines.append(line)

        org_section = "\n".join(org_tool_lines) if org_tool_lines else "(无)"
        has_external = bool(ext_tool_lines)

        parts = [org_context]

        # Runtime environment (compact)
        try:
            from ..config import settings
            tz_name = settings.scheduler_timezone
        except Exception:
            tz_name = "Asia/Shanghai"
        try:
            from datetime import timedelta, timezone
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = timezone(timedelta(hours=8))
            current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        shell_type = "PowerShell" if platform.system() == "Windows" else "bash"
        runtime_section = (
            f"## 运行环境\n"
            f"- 当前时间: {current_time}\n"
            f"- 操作系统: {platform.system()} {platform.release()}\n"
            f"- 工作目录: {workspace or os.getcwd()}\n"
            f"- Shell: {shell_type}"
        )
        if platform.system() == "Windows" and has_external:
            runtime_section += (
                "\n- Shell 注意: Windows 环境，复杂文本处理请用 write_file 写 Python 脚本"
                " + run_shell python xxx.py 执行，避免 PowerShell 转义问题"
            )
        parts.append(runtime_section)

        parts.append(f"## 组织协作工具（org_*）\n\n{org_section}")

        if has_external:
            ext_section = "\n".join(ext_tool_lines)
            parts.append(f"## 外部执行工具\n\n{ext_section}")

        parts.append(
            "参数带 * 为必填。用 get_tool_info(tool_name) 可查看工具完整参数。"
        )

        rule_delivery = (
            "6. **任务完成直接回复**。你是最高负责人，完成工作后在回复中总结成果即可，不要使用 org_submit_deliverable。\n"
            if is_root else
            "6. **任务交付流程**。收到任务后完成工作，用 org_submit_deliverable 提交给委派人验收。被打回时修改后重新提交。\n"
        )

        if has_external:
            parts.append(
                "## 行为准则\n\n"
                "1. **协作用 org_* 工具，执行用外部工具**。与同事沟通、委派、汇报用 org_* 工具；"
                "搜索信息、写文件、制定计划等实际执行工作用外部工具。\n"
                "2. **执行结果要共享**。用外部工具得到的重要结果，用 org_write_blackboard 写入黑板，方便同事查阅。\n"
                "3. **简洁回复**。完成工具调用后，用 1-2 句话总结结果即可。\n"
                "4. **先查再做**。不确定找谁时用 org_find_colleague；不确定流程时用 org_search_policy。\n"
                "5. **不要重复写入**。写黑板前先用 org_read_blackboard 检查是否已有相似内容。\n"
                + rule_delivery +
                "7. **缺少工具时申请**。如果任务需要你没有的工具，用 org_request_tools 向上级申请。"
            )
        else:
            parts.append(
                "## 行为准则\n\n"
                "1. **只使用上述 org_* 工具**。不要调用 write_file、read_file、run_shell 等非组织工具，它们不可用。\n"
                "2. **简洁回复**。完成工具调用后，用 1-2 句话总结结果即可。\n"
                "3. **先查再做**。不确定找谁时用 org_find_colleague；不确定流程时用 org_search_policy。\n"
                "4. **重要信息写黑板**。决策、方案、进度等用 org_write_blackboard 记录，方便同事查阅。\n"
                "5. **不要重复写入**。写黑板前先用 org_read_blackboard 检查是否已有相似内容。\n"
                + rule_delivery +
                "7. **缺少工具时申请**。如果任务需要你没有的工具，用 org_request_tools 向上级申请。"
            )

        # Core policy guardrails
        parts.append(
            "## 核心策略红线\n"
            "- 不编造信息。不确定时明确说明，不要虚构数据或结果。\n"
            "- 不假装执行。没有对应工具就不要声称已完成操作。\n"
            "- 不执行有害操作。不删除用户数据（除非明确要求），不访问敏感系统路径。"
        )

        lean_prompt = "\n\n".join(parts)

        ctx = getattr(agent, "_context", None)
        if ctx and hasattr(ctx, "system"):
            ctx.system = lean_prompt

    def _build_profile_for_node(self, node: OrgNode, org_prompt: str) -> Any:
        """Build an AgentProfile-like object for factory.create()."""
        from openakita.agents.profile import AgentProfile, SkillsMode

        if node.agent_profile_id:
            try:
                base = self._get_shared_profile(node.agent_profile_id)
                if base:
                    profile = AgentProfile(
                        id=f"org_node_{node.id}",
                        name=node.role_title,
                        icon=base.icon,
                        custom_prompt=org_prompt,
                        skills=node.skills if node.skills else base.skills,
                        skills_mode=SkillsMode(node.skills_mode) if node.skills_mode != "all" else base.skills_mode,
                        preferred_endpoint=node.preferred_endpoint or base.preferred_endpoint,
                    )
                    return profile
            except Exception as e:
                logger.warning(f"[OrgRuntime] Failed to load profile {node.agent_profile_id}: {e}")

        return AgentProfile(
            id=f"org_node_{node.id}",
            name=node.role_title,
            custom_prompt=org_prompt,
            skills=node.skills,
            skills_mode=SkillsMode(node.skills_mode) if node.skills_mode != "all" else SkillsMode.ALL,
            preferred_endpoint=node.preferred_endpoint,
        )

    def _get_shared_profile(self, profile_id: str) -> Any:
        """Get an AgentProfile from the shared ProfileStore via orchestrator."""
        try:
            from openakita.main import _orchestrator
            if _orchestrator and hasattr(_orchestrator, "_profile_store"):
                return _orchestrator._profile_store.get(profile_id)
        except (ImportError, AttributeError):
            pass
        try:
            from openakita.agents.profile import get_profile_store
            store = get_profile_store()
            return store.get(profile_id)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Message handler (called by messenger when a node receives a message)
    # ------------------------------------------------------------------

    async def _on_node_message(self, org_id: str, node_id: str, msg: OrgMessage) -> None:
        """Handle an incoming message for a node — activate and process."""
        if hasattr(msg, 'status') and msg.status == "expired":
            logger.debug(f"Skipping expired message {msg.id}")
            return

        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            return
        node = org.get_node(node_id)
        if not node or node.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
            return

        active_count = self._node_active_count(org_id, node_id)

        messenger = self.get_messenger(org_id)
        pending = messenger.get_pending_count(node_id) if messenger else 0

        def _mark_dispatched() -> None:
            if messenger:
                mb = messenger.get_mailbox(node_id)
                if mb and not mb.is_paused:
                    mb.mark_handler_processed(msg.id)

        if active_count >= self.max_concurrent_per_node:
            target_clone = self._try_route_to_clone(org, node, msg, pending)
            if target_clone:
                _mark_dispatched()
                task_prompt = self._format_incoming_message(msg)
                chain_id = msg.metadata.get("task_chain_id") or None
                await self._activate_and_run(org, target_clone, task_prompt, chain_id=chain_id)
                if messenger:
                    messenger.mark_processed(msg.id)
                return

            if node.auto_clone_enabled and pending >= node.auto_clone_threshold:
                new_clone = await self._scaler.maybe_auto_clone(org_id, node_id, pending)
                if new_clone:
                    _mark_dispatched()
                    self._register_clone_in_messenger(org_id, new_clone)
                    task_prompt = self._format_incoming_message(msg)
                    chain_id = msg.metadata.get("task_chain_id") or None
                    await self._activate_and_run(org, new_clone, task_prompt, chain_id=chain_id)
                    if messenger:
                        messenger.mark_processed(msg.id)
                    return

            logger.info(
                f"[OrgRuntime] Node {node_id} already has {active_count} "
                f"active tasks, message {msg.id} stays in mailbox"
            )
            return

        _mark_dispatched()
        task_prompt = self._format_incoming_message(msg)
        chain_id = msg.metadata.get("task_chain_id") or ""
        await self._activate_and_run(org, node, task_prompt, chain_id=chain_id or None)
        if messenger:
            messenger.mark_processed(msg.id)

    def _try_route_to_clone(
        self, org: Organization, node: OrgNode, msg: OrgMessage, pending: int
    ) -> OrgNode | None:
        """Try to find an available clone for this task."""
        clones = [n for n in org.nodes if n.clone_source == node.id
                   and n.status not in (NodeStatus.FROZEN, NodeStatus.OFFLINE)]
        if not clones:
            return None

        chain_id = msg.metadata.get("task_chain_id")
        if chain_id:
            messenger = self.get_messenger(org.id)
            if messenger:
                affinity = messenger.get_task_affinity(chain_id)
                if affinity:
                    for c in clones:
                        if c.id == affinity and c.status == NodeStatus.IDLE:
                            return c

        idle_clones = [c for c in clones if c.status == NodeStatus.IDLE]
        if idle_clones:
            return idle_clones[0]

        return None

    def _make_message_handler(self, org_id: str, node_id: str) -> Any:
        async def _handler(msg: OrgMessage, _nid=node_id, _oid=org_id):
            task_key = f"{_nid}:{msg.id}"
            task = asyncio.create_task(self._on_node_message(_oid, _nid, msg))
            self._running_tasks.setdefault(_oid, {})[task_key] = task
            task.add_done_callback(
                lambda _t, _o=_oid, _k=task_key: self._running_tasks.get(_o, {}).pop(_k, None)
            )
        return _handler

    def _register_clone_in_messenger(self, org_id: str, clone: OrgNode) -> None:
        """Register a newly created clone in the messenger system."""
        messenger = self.get_messenger(org_id)
        if not messenger:
            return
        org = self._active_orgs.get(org_id)
        if org:
            messenger.update_org(org)
        messenger.register_node(clone.id, self._make_message_handler(org_id, clone.id))

    def _format_incoming_message(self, msg: OrgMessage) -> str:
        """Format an OrgMessage into a prompt for the receiving agent."""
        type_labels = {
            MsgType.TASK_ASSIGN: "收到任务",
            MsgType.TASK_RESULT: "收到任务结果",
            MsgType.TASK_DELIVERED: "收到任务交付",
            MsgType.TASK_ACCEPTED: "任务已通过验收",
            MsgType.TASK_REJECTED: "任务被打回",
            MsgType.REPORT: "收到汇报",
            MsgType.QUESTION: "收到提问",
            MsgType.ANSWER: "收到回答",
            MsgType.ESCALATE: "收到上报",
            MsgType.BROADCAST: "收到组织公告",
            MsgType.DEPT_BROADCAST: "收到部门公告",
            MsgType.FEEDBACK: "收到反馈",
            MsgType.HANDSHAKE: "收到握手请求",
        }
        label = type_labels.get(msg.msg_type, "收到消息")
        prefix = f"[{label}] 来自 {msg.from_node}"
        if msg.reply_to:
            prefix += f" (回复消息 {msg.reply_to})"

        chain_id = msg.metadata.get("task_chain_id", "")
        if chain_id:
            prefix += f" [任务链: {chain_id[:12]}]"

        extra = ""
        if msg.msg_type == MsgType.TASK_DELIVERED:
            deliverable = msg.metadata.get("deliverable", "")
            summary = msg.metadata.get("summary", "")
            if deliverable:
                extra = f"\n交付内容: {deliverable}"
            if summary:
                extra += f"\n工作简述: {summary}"
            extra += "\n请用 org_accept_deliverable 或 org_reject_deliverable 进行验收。"
        elif msg.msg_type == MsgType.TASK_REJECTED:
            reason = msg.metadata.get("rejection_reason", "")
            if reason:
                extra = f"\n打回原因: {reason}\n请根据反馈修改后重新用 org_submit_deliverable 提交。"
        elif msg.msg_type == MsgType.TASK_ASSIGN:
            if chain_id:
                extra = f"\n完成后请用 org_submit_deliverable 提交交付物，task_chain_id={chain_id}"
            else:
                extra = "\n完成后请用 org_submit_deliverable 提交交付物。"

        return f"{prefix}:\n{msg.content}{extra}"

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_org(self, org_id: str) -> Organization | None:
        return self._active_orgs.get(org_id) or self._manager.get(org_id)

    def get_messenger(self, org_id: str) -> OrgMessenger | None:
        return self._messengers.get(org_id)

    def get_blackboard(self, org_id: str) -> OrgBlackboard | None:
        return self._blackboards.get(org_id)

    def get_event_store(self, org_id: str) -> OrgEventStore:
        if org_id not in self._event_stores:
            org_dir = self._manager._org_dir(org_id)
            self._event_stores[org_id] = OrgEventStore(org_dir, org_id)
        return self._event_stores[org_id]

    def get_inbox(self, org_id: str) -> OrgInbox:
        return self._inbox

    def get_scaler(self) -> OrgScaler:
        return self._scaler

    def get_heartbeat(self) -> OrgHeartbeat:
        return self._heartbeat

    def get_scheduler(self) -> OrgNodeScheduler:
        return self._scheduler

    def get_notifier(self) -> OrgNotifier:
        return self._notifier

    def get_reporter(self) -> OrgReporter:
        return self._reporter

    def get_policies(self, org_id: str) -> OrgPolicies:
        if org_id not in self._policies:
            from .policies import OrgPolicies as _P
            org_dir = self._manager._org_dir(org_id)
            self._policies[org_id] = _P(org_dir)
        return self._policies[org_id]

    def _get_identity(self, org_id: str) -> OrgIdentity:
        if org_id not in self._identities:
            org_dir = self._manager._org_dir(org_id)
            global_identity = None
            try:
                from openakita.config import settings
                global_identity = Path(settings.project_root) / "identity"
            except Exception:
                pass
            self._identities[org_id] = OrgIdentity(org_dir, global_identity)
        return self._identities[org_id]

    # ------------------------------------------------------------------
    # Node status management
    # ------------------------------------------------------------------

    def _set_node_status(
        self, org: Organization, node: OrgNode,
        new_status: NodeStatus, reason: str = "",
    ) -> None:
        """Set node status with audit trail (event_store + log)."""
        old_status = node.status
        if old_status == new_status:
            return
        if node.status == NodeStatus.FROZEN and new_status != NodeStatus.FROZEN:
            if reason != "unfreeze":
                logger.debug(f"Skipping status change for frozen node {node.id}")
                return
        key = f"{org.id}:{node.id}"
        if new_status == NodeStatus.BUSY:
            self._node_busy_since[key] = time.monotonic()
        elif old_status == NodeStatus.BUSY:
            self._node_busy_since.pop(key, None)
        node.status = new_status
        self.get_event_store(org.id).emit(
            "node_status_change", node.id,
            {"from": old_status.value, "to": new_status.value, "reason": reason},
        )
        logger.info(
            f"[OrgRuntime] Node {node.id}: {old_status.value} -> {new_status.value}"
            + (f" ({reason})" if reason else "")
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _activate_org(self, org: Organization) -> None:
        """Set up runtime infrastructure for an organization."""
        org_dir = self._manager._org_dir(org.id)
        self._active_orgs[org.id] = org
        self._messengers[org.id] = OrgMessenger(org, org_dir)
        self._blackboards[org.id] = OrgBlackboard(org_dir, org.id)
        self._event_stores[org.id] = OrgEventStore(org_dir, org.id)

        messenger = self._messengers[org.id]
        for node in org.nodes:
            messenger.register_handler(node.id, self._make_message_handler(org.id, node.id))

        async def _on_deadlock(cycles: list[list[str]], _oid=org.id) -> None:
            es = self.get_event_store(_oid)
            for cycle in cycles:
                es.emit("conflict_detected", "system", {
                    "type": "deadlock", "cycle": cycle,
                })
            inbox = self.get_inbox(_oid)
            inbox.push_warning(
                _oid, "system",
                title="检测到死锁",
                body=f"以下节点间存在循环等待: {cycles}",
            )
        messenger.set_deadlock_handler(_on_deadlock)

        task = asyncio.ensure_future(messenger.start_background_tasks())
        task.add_done_callback(
            lambda t: logger.error(f"[OrgRuntime] Messenger bg tasks failed: {t.exception()}")
            if t.done() and not t.cancelled() and t.exception() else None
        )

    async def _deactivate_org(self, org_id: str) -> None:
        messenger = self._messengers.get(org_id)
        if messenger:
            try:
                await messenger.stop_background_tasks()
            except Exception as e:
                logger.error(f"[OrgRuntime] Messenger stop failed for {org_id}: {e}")
        self._active_orgs.pop(org_id, None)
        self._messengers.pop(org_id, None)
        self._blackboards.pop(org_id, None)
        self._event_stores.pop(org_id, None)
        self._identities.pop(org_id, None)
        self._policies.pop(org_id, None)
        self._org_quota_failures.pop(org_id, None)

        keys_to_remove = [k for k in self._agent_cache if k.startswith(f"{org_id}:")]
        for k in keys_to_remove:
            self._agent_cache.pop(k, None)
        for k in list(self._node_busy_since.keys()):
            if k.startswith(f"{org_id}:"):
                self._node_busy_since.pop(k, None)
        for k in list(self._node_last_activity.keys()):
            if k.startswith(f"{org_id}:"):
                self._node_last_activity.pop(k, None)
        for k in list(self._node_current_chain.keys()):
            if k.startswith(f"{org_id}:"):
                self._node_current_chain.pop(k, None)

    def _get_save_lock(self, org_id: str) -> asyncio.Lock:
        lock = self._save_locks.get(org_id)
        if lock is None:
            lock = asyncio.Lock()
            self._save_locks[org_id] = lock
        return lock

    async def _save_org(self, org: Organization) -> None:
        async with self._get_save_lock(org.id):
            org.updated_at = _now_iso()
            try:
                if not self._manager.save_direct(org):
                    logger.warning(
                        f"[OrgRuntime] _save_org skipped — org {org.id} no longer on disk"
                    )
                    self._active_orgs.pop(org.id, None)
            except FileNotFoundError:
                logger.warning(
                    f"[OrgRuntime] _save_org race — org {org.id} disappeared mid-write"
                )
                self._active_orgs.pop(org.id, None)

    def _save_state(self, org_id: str) -> None:
        org = self._active_orgs.get(org_id)
        if not org:
            return
        state = {
            "status": org.status.value,
            "saved_at": _now_iso(),
            "node_statuses": {n.id: n.status.value for n in org.nodes},
        }
        self._manager.save_state(org_id, state)

    async def _recover_pending_tasks(self, org: Organization) -> None:
        """Reset stale node statuses and orphan tasks after a restart.

        After a process restart, in-memory agents are gone. Any node still
        marked busy/waiting/error in the persisted org.json is stale and must
        be reset to IDLE so the node can accept new work.  We check the live
        org object (loaded from org.json) rather than only the state.json
        snapshot, because state.json is only written during graceful shutdown
        and may be missing or outdated after a crash.

        We also reset any ``in_progress`` tasks assigned to recovered nodes
        back to ``todo`` so the orchestrator can re-dispatch them.
        """
        recovered_count = 0
        stale_statuses = {NodeStatus.BUSY, NodeStatus.WAITING, NodeStatus.ERROR}
        recovered_node_ids: set[str] = set()

        for node in org.nodes:
            if node.status in stale_statuses:
                self._set_node_status(org, node, NodeStatus.IDLE, "restart_cleanup")
                self._agent_cache.pop(f"{org.id}:{node.id}", None)
                recovered_node_ids.add(node.id)
                recovered_count += 1

        if recovered_count > 0:
            await self._save_org(org)
            logger.info(f"[OrgRuntime] Recovered {recovered_count} stale nodes for {org.name}")

        self._recover_orphan_tasks(org, recovered_node_ids)

    def _recover_orphan_tasks(
        self, org: Organization, recovered_node_ids: set[str]
    ) -> None:
        """Reset in_progress tasks whose assignee nodes are now idle.

        Called after node recovery to maintain task ↔ node consistency.
        Tasks are reset to ``todo`` so they can be re-dispatched.
        """
        from openakita.orgs.models import TaskStatus
        from openakita.orgs.project_store import ProjectStore

        try:
            org_dir = self._manager._org_dir(org.id)
            store = ProjectStore(org_dir)
        except Exception as exc:
            logger.debug("[OrgRuntime] Cannot open ProjectStore for %s: %s", org.id, exc)
            return

        orphan_tasks = store.all_tasks(status="in_progress")
        reset_count = 0
        for task_dict in orphan_tasks:
            assignee = task_dict.get("assignee_node_id", "")
            if not assignee:
                continue
            node_is_idle = any(n.id == assignee and n.status == NodeStatus.IDLE for n in org.nodes)
            if not node_is_idle:
                continue
            if recovered_node_ids and assignee not in recovered_node_ids:
                continue
            task_id = task_dict.get("id", "")
            project_id = task_dict.get("project_id", "")
            if not task_id or not project_id:
                continue
            store.update_task(project_id, task_id, {"status": TaskStatus.TODO})
            reset_count += 1
            logger.info(
                "[OrgRuntime] Reset orphan task %s (assignee=%s) to todo in org %s",
                task_id[:12], assignee, org.name,
            )

        if reset_count > 0:
            logger.info(
                "[OrgRuntime] Reset %d orphan tasks for org %s", reset_count, org.name
            )

    def _evict_expired_agents(self) -> None:
        expired = [k for k, v in self._agent_cache.items() if v.expired]
        for k in expired:
            self._agent_cache.pop(k, None)

    def evict_node_agent(self, org_id: str, node_id: str) -> None:
        """Evict a specific node's cached agent so it gets rebuilt with fresh config."""
        cache_key = f"{org_id}:{node_id}"
        self._agent_cache.pop(cache_key, None)

    @staticmethod
    def _connect_node_mcp_servers(agent: Any, mcp_servers: list[str]) -> None:
        """Best-effort connect MCP servers listed on the node."""
        try:
            client = getattr(agent, "mcp_client", None)
            if not client:
                return
            for server_name in mcp_servers:
                if hasattr(client, "connect"):
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(client.connect(server_name))
                        task.add_done_callback(
                            lambda t, s=server_name: (
                                logger.warning(f"[OrgRuntime] MCP connect '{s}' failed: {t.exception()}")
                                if t.exception() else None
                            )
                        )
                    except RuntimeError:
                        pass
        except Exception as e:
            logger.debug(f"[OrgRuntime] MCP connect for node failed: {e}")

    # ------------------------------------------------------------------
    # Task completion hook & idle probe
    # ------------------------------------------------------------------

    def _node_active_count(self, org_id: str, node_id: str) -> int:
        """Count running (not-done) tasks for a node."""
        running = self._running_tasks.get(org_id, {})
        return sum(
            1 for k, t in running.items()
            if k.startswith(f"{node_id}:") and not t.done()
        )

    async def _drain_node_pending(
        self, org: Organization, node: OrgNode, *, max_msgs: int = 0,
    ) -> int:
        """Drain pending messages from a node's mailbox.

        Processes up to *max_msgs* messages (0 = fill all available
        concurrency slots).  Returns the number of messages dispatched.
        """
        messenger = self.get_messenger(org.id)
        if not messenger:
            return 0
        mailbox = messenger.get_mailbox(node.id)
        if not mailbox or mailbox.pending_count <= 0:
            return 0

        active = self._node_active_count(org.id, node.id)
        slots = self.max_concurrent_per_node - active
        if slots <= 0:
            return 0
        if max_msgs > 0:
            slots = min(slots, max_msgs)

        dispatched = 0
        max_iterations = slots + mailbox._queue.qsize()
        for _ in range(max_iterations):
            if mailbox.pending_count <= 0 or dispatched >= slots:
                break
            msg = await mailbox.get(timeout=0.5)
            if not msg:
                break
            if mailbox.is_handler_processed(msg.id):
                mailbox.consume_phantom(msg.id)
                continue
            logger.info(
                f"[OrgRuntime] Draining pending message {msg.id} for {node.id} "
                f"(remaining: {mailbox.pending_count})"
            )
            task_prompt = self._format_incoming_message(msg)
            chain_id = msg.metadata.get("task_chain_id") or None
            await self._activate_and_run(org, node, task_prompt, chain_id=chain_id)
            dispatched += 1
        return dispatched

    async def _post_task_hook(self, org: Organization, node: OrgNode) -> None:
        """After a node finishes, process pending messages or notify parent.

        Priority order:
        1. Drain THIS node's own pending messages (it just freed a slot).
        2. If parent has pending messages (e.g. deliverables from children),
           drain those instead of creating a new "completion notification".
        3. Only when parent has NO pending messages, send the notification
           (rate-limited by cooldown to prevent cascade).
        """
        try:
            await asyncio.sleep(2)

            if self._suppress_post_hook.get(org.id):
                return

            org = self.get_org(org.id)
            if not org or org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                return
            node = org.get_node(node.id)
            if not node or node.status != NodeStatus.IDLE:
                return

            if await self._drain_node_pending(org, node):
                return

            parent = org.get_parent(node.id)
            if not parent:
                return
            if parent.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
                return

            messenger = self.get_messenger(org.id)
            parent_pending = messenger.get_pending_count(parent.id) if messenger else 0

            if parent_pending > 0:
                if parent.status == NodeStatus.IDLE:
                    await self._drain_node_pending(org, parent)
                return

            if parent.status == NodeStatus.BUSY:
                return

            cooldown_key = f"{org.id}:{parent.id}"
            now = time.monotonic()
            last = self._post_hook_cooldown.get(cooldown_key, 0)
            if now - last < 15:
                return
            self._post_hook_cooldown[cooldown_key] = now

            role_title = node.role_title or node.id
            prompt = (
                f"[任务完成通知] {role_title} 刚完成了一项任务。\n"
                f"请查看是否有待处理的交付物需要验收。\n"
                f"如果没有待验收的内容，无需任何操作。\n"
                f"⚠️ 不要主动发起新任务或扩展工作范围。"
            )
            await self._activate_and_run(org, parent, prompt)
        except Exception as e:
            logger.debug(f"[OrgRuntime] Post-task hook error: {e}")

    _STOP_KEYWORDS = frozenset({
        "暂停", "停止", "取消", "别做了", "先不做", "到此为止",
        "不要继续", "停下来", "先暂停", "不用做了", "够了",
    })

    def _is_stop_intent(self, content: str) -> bool:
        return any(kw in content for kw in self._STOP_KEYWORDS)

    async def _soft_stop_org(self, org_id: str) -> None:
        self._suppress_post_hook[org_id] = True
        messenger = self.get_messenger(org_id)
        org = self.get_org(org_id)
        if not org:
            return
        for node in org.nodes:
            if node.level > 0 and node.status == NodeStatus.BUSY:
                try:
                    await self.cancel_node_task(org_id, node.id)
                except Exception:
                    pass
            if messenger:
                messenger.clear_node_pending(node.id)
        self.get_event_store(org_id).emit("soft_stop", "user", {})

    def _has_active_delegations(self, org_id: str, root_node_id: str) -> bool:
        org = self.get_org(org_id)
        if not org:
            return False
        for node in org.nodes:
            if node.id != root_node_id and node.status in (NodeStatus.BUSY, NodeStatus.WAITING):
                return True
        messenger = self.get_messenger(org_id)
        if messenger:
            for node in org.nodes:
                if node.id != root_node_id and messenger.get_pending_count(node.id) > 0:
                    return True
        return False

    async def _wait_delegation_completion(
        self, org_id: str, root_node_id: str, timeout: int = 300,
    ) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(5)
            org = self.get_org(org_id)
            if not org or org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                break
            root = org.get_node(root_node_id)
            if not root:
                break
            if root.status == NodeStatus.IDLE and not self._has_active_delegations(org_id, root_node_id):
                messenger = self.get_messenger(org_id)
                root_pending = messenger.get_pending_count(root_node_id) if messenger else 0
                if root_pending == 0:
                    return self._latest_root_result.pop(org_id, None)
        return self._latest_root_result.pop(org_id, None)

    async def _health_check_loop(self, org_id: str) -> None:
        """Command mode: only check node health, recover ERROR nodes to IDLE.
        No proactive work or idle probing."""
        while True:
            try:
                await asyncio.sleep(60)
                org = self.get_org(org_id)
                if not org or org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                    break

                recovered_nodes = []
                for node in org.nodes:
                    if node.status == NodeStatus.ERROR:
                        self._set_node_status(org, node, NodeStatus.IDLE, "health_check_recovery")
                        self._agent_cache.pop(f"{org_id}:{node.id}", None)
                        recovered_nodes.append(node)
                await self._save_org(org)
                for node in recovered_nodes:
                    await self._broadcast_ws("org:node_status", {
                        "org_id": org_id, "node_id": node.id,
                        "status": "idle", "current_task": "",
                    })

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[OrgRuntime] Health check error for {org_id}: {e}")
                await asyncio.sleep(60)

    async def _watchdog_notify_delegator(
        self, org: Organization, node: OrgNode, reason: str, stuck_secs: int,
    ) -> None:
        """Notify the parent (delegator) node when watchdog recovers a stuck/error child."""
        parent = org.get_parent(node.id)
        if not parent:
            return
        messenger = self.get_messenger(org.id)
        if not messenger:
            return
        reason_text = {
            "stuck_busy": f"BUSY 状态无活跃度持续 {stuck_secs} 秒",
            "error_not_recovering": "持续 ERROR 状态未恢复",
        }.get(reason, reason)
        msg = OrgMessage(
            org_id=org.id,
            from_node="system",
            to_node=parent.id,
            msg_type=MsgType.FEEDBACK,
            content=(
                f"[看门狗通知] 您的下属 {node.role_title}({node.id}) "
                f"因[{reason_text}]被自动恢复。"
                f"该节点已重置为空闲状态，之前的任务已被中断。"
                f"如有未完成的委派任务，请重新分配或跟进。"
            ),
        )
        await messenger.send(msg)

    async def _watchdog_loop(self, org_id: str) -> None:
        """Monitor all nodes for stuck BUSY, unrecovered ERROR, and silence in autonomous mode."""
        while True:
            try:
                org = self.get_org(org_id)
                if not org:
                    logger.info(f"[OrgRuntime] Org {org_id} no longer exists, stopping watchdog")
                    break
                interval = getattr(org, "watchdog_interval_s", 30) or 30
                await asyncio.sleep(interval)

                org = self.get_org(org_id)
                if not org:
                    logger.info(f"[OrgRuntime] Org {org_id} no longer exists, stopping watchdog")
                    break
                if org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                    continue
                if not getattr(org, "watchdog_enabled", False):
                    break

                stuck_threshold = getattr(org, "watchdog_stuck_threshold_s", 1800) or 1800
                silence_threshold = getattr(org, "watchdog_silence_threshold_s", 1800) or 1800
                mode = getattr(org, "operation_mode", "command") or "command"
                now = time.monotonic()

                for node in org.nodes:
                    if node.is_clone:
                        continue
                    key = f"{org_id}:{node.id}"

                    if node.status == NodeStatus.BUSY:
                        busy_since = self._node_busy_since.get(key, now)
                        if (now - busy_since) >= stuck_threshold:
                            org_tasks = self._running_tasks.get(org_id, {})
                            for task_key, task in list(org_tasks.items()):
                                if task_key.startswith(f"{node.id}:") and not task.done():
                                    task.cancel()
                                    try:
                                        await task
                                    except (asyncio.CancelledError, Exception):
                                        pass
                                    org_tasks.pop(task_key, None)
                            self._agent_cache.pop(key, None)
                            self._set_node_status(org, node, NodeStatus.IDLE, "watchdog_recovery")
                            stuck_secs = int(now - busy_since)
                            self.get_event_store(org_id).emit(
                                "watchdog_recovery", node.id,
                                {"reason": "stuck_busy", "stuck_secs": stuck_secs},
                            )
                            await self._save_org(org)
                            await self._broadcast_ws("org:node_status", {
                                "org_id": org_id, "node_id": node.id,
                                "status": "idle", "current_task": "",
                            })
                            await self._broadcast_ws("org:watchdog_recovery", {
                                "org_id": org_id, "node_id": node.id,
                                "reason": "stuck_busy", "stuck_secs": stuck_secs,
                            })
                            await self._watchdog_notify_delegator(
                                org, node, "stuck_busy", stuck_secs,
                            )
                            logger.warning(
                                f"[OrgRuntime] Watchdog recovered stuck node {node.id} "
                                f"(BUSY for {stuck_secs}s)"
                            )

                    elif node.status == NodeStatus.ERROR:
                        self._set_node_status(org, node, NodeStatus.IDLE, "watchdog_recovery")
                        self._agent_cache.pop(key, None)
                        self.get_event_store(org_id).emit(
                            "watchdog_recovery", node.id, {"reason": "error_not_recovering"},
                        )
                        await self._save_org(org)
                        await self._broadcast_ws("org:node_status", {
                            "org_id": org_id, "node_id": node.id,
                            "status": "idle", "current_task": "",
                        })
                        await self._broadcast_ws("org:watchdog_recovery", {
                            "org_id": org_id, "node_id": node.id,
                            "reason": "error_not_recovering",
                        })
                        await self._watchdog_notify_delegator(
                            org, node, "error_not_recovering", 0,
                        )

                if mode == "autonomous":
                    last_activity = self._heartbeat._last_activity.get(org_id, 0)
                    if last_activity > 0 and (now - last_activity) >= silence_threshold:
                        roots = org.get_root_nodes()
                        if roots:
                            root = roots[0]
                            if root.status == NodeStatus.IDLE:
                                prompt = (
                                    "[看门狗激活] 组织已静默较长时间。请查看黑板和当前进展，"
                                    "决定是否需要推进工作或分配新任务。"
                                )
                                self._heartbeat.record_activity(org_id)
                                asyncio.ensure_future(
                                    self._activate_and_run(org, root, prompt)
                                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[OrgRuntime] Watchdog error for {org_id}: {e}")
                await asyncio.sleep(30)

    async def _idle_probe_loop(self, org_id: str) -> None:
        """Periodically check for idle nodes and prompt them to seek work.

        Uses per-node adaptive thresholds: each node's threshold grows after
        being probed (120s → 180s → 270s → ... max 600s), and resets when
        the node becomes busy again (indicating it received work).
        """
        node_thresholds: dict[str, float] = {}
        node_last_probed: dict[str, float] = {}
        base_threshold = 120.0

        while True:
            try:
                await asyncio.sleep(30)
                org = self.get_org(org_id)
                if not org or org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                    break

                now = time.monotonic()
                for node in org.nodes:
                    if node.status != NodeStatus.IDLE:
                        node_thresholds.pop(node.id, None)
                        node_last_probed.pop(node.id, None)
                        continue
                    if node.is_clone:
                        continue

                    cache_key = f"{org_id}:{node.id}"
                    last_active = self._node_last_activity.get(cache_key, 0)
                    if last_active <= 0:
                        cached = self._agent_cache.get(cache_key)
                        last_active = cached.last_used if cached else 0
                    idle_secs = now - last_active if last_active > 0 else 0

                    threshold = node_thresholds.get(node.id, base_threshold)

                    if 0 < idle_secs >= threshold:
                        last_probe = node_last_probed.get(node.id, 0)
                        if last_probe > 0 and (now - last_probe) < threshold * 0.8:
                            continue

                        messenger = self.get_messenger(org_id)
                        pending = messenger.get_pending_count(node.id) if messenger else 0
                        if pending > 0:
                            continue

                        roots = org.get_root_nodes()
                        is_root = node.id in [r.id for r in roots]

                        if is_root:
                            prompt = (
                                f"[空闲检查] 你已空闲 {int(idle_secs)} 秒。\n"
                                f"请查看组织黑板（org_read_blackboard），确认是否有待推进的工作。\n"
                                f"如果有未完成的目标，请安排下一步任务。如果一切正常，简要说明当前状态即可。"
                            )
                        else:
                            prompt = (
                                f"[空闲检查] 你已空闲 {int(idle_secs)} 秒。\n"
                                f"请查看是否有待办工作，或向上级汇报空闲状态以获取新任务。"
                            )

                        node_last_probed[node.id] = now
                        node_thresholds[node.id] = min(threshold * 1.5, 600)
                        await self._activate_and_run(org, node, prompt)
                        break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[OrgRuntime] Idle probe error for {org_id}: {e}")
                await asyncio.sleep(30)

    async def _broadcast_ws(self, event: str, data: dict) -> None:
        try:
            from openakita.api.routes.websocket import broadcast_event
            await broadcast_event(event, data)
        except Exception:
            logger.debug("[OrgRuntime] _broadcast_ws failed: %s %s", event, data, exc_info=True)

    # ------------------------------------------------------------------
    # Tool call integration
    # ------------------------------------------------------------------

    async def handle_org_tool(
        self, tool_name: str, arguments: dict, org_id: str, node_id: str
    ) -> str:
        """Public entry point for org tool execution."""
        return await self._tool_handler.handle(tool_name, arguments, org_id, node_id)

    def _register_org_tool_handler(
        self, agent: Any, org_id: str, node_id: str
    ) -> None:
        """Patch agent's ToolExecutor to intercept org_* tool calls and bridge plan tools.

        The ReAct execution path is:
            execute_batch → execute_tool_with_policy → _execute_tool_impl

        We patch ``execute_tool_with_policy`` so that org_* calls are handled
        **before** both the ``_check_todo_required`` gate and the
        ``handler_registry.has_tool()`` check in ``_execute_tool_impl``.
        Without this, org tools are either blocked by the mandatory-todo
        policy or rejected as "unknown tools".
        """
        if not hasattr(agent, "reasoning_engine"):
            return
        engine = agent.reasoning_engine
        if not hasattr(engine, "_tool_executor"):
            return
        executor = engine._tool_executor

        original_with_policy = executor.execute_tool_with_policy
        tool_handler = self._tool_handler

        async def _patched_with_policy(
            tool_name: str, tool_input: dict, policy_result: Any = None,
            *, session_id: str | None = None,
        ) -> str:
            self._node_last_activity[f"{org_id}:{node_id}"] = time.monotonic()
            if tool_name.startswith("org_"):
                return await tool_handler.handle(tool_name, tool_input, org_id, node_id)
            result = await original_with_policy(
                tool_name, tool_input, policy_result, session_id=session_id,
            )
            if tool_name in ("create_plan", "update_plan_step", "complete_plan"):
                chain_id = getattr(agent, "_org_context", {}).get("current_chain_id") or ""
                if chain_id:
                    tool_handler._bridge_plan_to_task(
                        org_id, node_id, tool_name, tool_input, result, chain_id=chain_id
                    )
            if tool_name in ("write_file", "generate_image"):
                try:
                    ws = getattr(agent, "_org_context", {}).get("workspace")
                    self._record_file_output(
                        org_id, node_id, tool_name, tool_input, result,
                        workspace=ws,
                    )
                except Exception:
                    logger.debug(
                        "[OrgRuntime] failed to record file output", exc_info=True,
                    )
            return result

        executor.execute_tool_with_policy = _patched_with_policy

    # ------------------------------------------------------------------
    # File output tracking → blackboard
    # ------------------------------------------------------------------

    _FILE_EXT_LABELS: dict[str, str] = {
        ".md": "Markdown 文档",
        ".txt": "文本文件",
        ".csv": "CSV 数据",
        ".json": "JSON 数据",
        ".py": "Python 脚本",
        ".js": "JavaScript 脚本",
        ".html": "HTML 页面",
        ".pdf": "PDF 文档",
        ".png": "PNG 图片",
        ".jpg": "JPEG 图片",
        ".jpeg": "JPEG 图片",
        ".gif": "GIF 图片",
        ".webp": "WebP 图片",
        ".svg": "SVG 图形",
        ".xlsx": "Excel 表格",
        ".docx": "Word 文档",
        ".zip": "压缩包",
    }

    def _record_file_output(
        self,
        org_id: str,
        node_id: str,
        tool_name: str,
        tool_input: dict,
        result: str,
        *,
        workspace: Path | None = None,
    ) -> None:
        """After write_file / generate_image succeeds, write a RESOURCE
        entry to the org blackboard so users can see and download files."""
        import json as _json

        file_path: str | None = None

        if tool_name == "write_file":
            if "❌" in result:
                return
            file_path = tool_input.get("path", "")
        elif tool_name == "generate_image":
            try:
                data = _json.loads(result)
                if not data.get("ok"):
                    return
                file_path = data.get("saved_to", "")
            except Exception:
                return

        if not file_path:
            return

        p = Path(file_path)
        if not p.is_absolute():
            base = workspace or Path.cwd()
            p = (base / p).resolve()
        else:
            p = p.resolve()

        if not p.exists() or not p.is_file():
            return

        size_bytes = p.stat().st_size
        filename = p.name
        ext = p.suffix.lower()
        ext_label = self._FILE_EXT_LABELS.get(ext, "文件")

        attachment = {
            "filename": filename,
            "path": str(p),
            "size_bytes": size_bytes,
        }

        bb = self.get_blackboard(org_id)
        if not bb:
            return

        entry = bb.write_org(
            content=f"📎 产出{ext_label}：**{filename}**",
            source_node=node_id,
            memory_type=MemoryType.RESOURCE,
            tags=["file_output", ext.lstrip(".")],
            importance=0.6,
            attachments=[attachment],
        )

        if entry:
            asyncio.ensure_future(self._broadcast_ws("org:blackboard_update", {
                "org_id": org_id, "scope": "org", "node_id": node_id,
                "memory_type": "resource",
            }))
