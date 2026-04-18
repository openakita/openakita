"""
OrgRuntime — organization runtime engine

Responsible for organization lifecycle management, on-demand activation of node Agents,
task scheduling, message dispatch, and WebSocket event broadcasting.
Integrates heartbeat, scheduled tasks, scaling, inbox, notification, policy management and other subsystems.
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
from .failure_diagnoser import format_human_summary, summarize as _diagnose_failure
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
from .tools import ORG_NODE_TOOLS, build_org_node_tools

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


class UserCommandTracker:
    """Track the lifecycle of a single user command across its delegation chains.

    A user command is considered **truly complete** when:
      - all chains that were opened *by or under this command's root* are closed
        (accepted / rejected / cancelled), AND
      - the root node is IDLE, AND
      - the root's inbox has no pending messages, AND
      - no other nodes under this org are still BUSY/WAITING with pending work.

    This tracker is event-driven: `register_chain`/`unregister_chain` are called
    from `_handle_org_delegate_task` and `_mark_chain_closed` respectively.
    Completion is signalled via :pyattr:`completed`.

    The :pyattr:`last_progress_at` timestamp is refreshed by `_touch()` whenever
    any progress signal fires (node status change, org tool call, messenger
    dispatch, chain event). It is consumed **only** by the command watchdog to
    decide whether to emit a stuck warning or to soft-stop the organization.
    It does **not** participate in completion judgement.
    """

    __slots__ = (
        "org_id",
        "root_node_id",
        "command_id",
        "open_chains",
        "completed",
        "last_progress_at",
        "started_at",
        "warned_stuck",
        "auto_stopped",
    )

    def __init__(
        self,
        org_id: str,
        root_node_id: str,
        command_id: str | None = None,
    ) -> None:
        self.org_id = org_id
        self.root_node_id = root_node_id
        self.command_id = command_id
        self.open_chains: set[str] = set()
        self.completed: asyncio.Event = asyncio.Event()
        now = time.monotonic()
        self.last_progress_at: float = now
        self.started_at: float = now
        self.warned_stuck: bool = False
        self.auto_stopped: bool = False

    def _touch(self) -> None:
        self.last_progress_at = time.monotonic()
        self.warned_stuck = False

    def register_chain(self, chain_id: str) -> None:
        if not chain_id:
            return
        self.open_chains.add(chain_id)
        self._touch()
        self.completed.clear()

    def unregister_chain(self, chain_id: str) -> None:
        if not chain_id:
            return
        self.open_chains.discard(chain_id)
        self._touch()


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
        # Set of accepted/rejected/cancelled task chains (per org). Used for:
        #   1) suppressing messages on closed chains from re-waking agent ReAct;
        #   2) blocking delegate/submit on closed chains;
        #   3) other chain-lifecycle idempotency checks.
        # Bounded: at most N recent entries per org, to prevent set growth in long-running orgs.
        self._closed_chains: dict[str, "OrderedDict[str, float]"] = {}
        self._closed_chain_max_per_org: int = 512
        self.max_concurrent_per_node: int = 2
        self._idle_tasks: dict[str, asyncio.Task] = {}

        # Org-level concurrency control: limit the number of nodes simultaneously active per org
        self.max_concurrent_nodes_per_org: int = 5
        self._org_semaphores: dict[str, asyncio.Semaphore] = {}

        self._save_locks: dict[str, asyncio.Lock] = {}

        self._node_consecutive_failures: dict[str, int] = {}
        self._org_quota_failures: dict[str, int] = {}

        self._post_hook_cooldown: dict[str, float] = {}
        self._suppress_post_hook: dict[str, bool] = {}
        self._latest_root_result: dict[str, dict] = {}

        # User-command lifecycle tracking: key=(org_id, root_node_id) -> UserCommandTracker.
        # Created by send_command at command start, removed at end. Used for event-driven
        # completion detection (all chains closed + root IDLE + root inbox empty) and to
        # provide `last_progress_at` / `warned_stuck` state for the watchdog.
        self._active_user_cmd: dict[tuple[str, str], UserCommandTracker] = {}

        # Origin tag of the "next activation" of a root node; gates writes to _latest_root_result.
        # key = f"{org_id}:{node_id}", value in {"user_command", "task_delivered",
        # "delivery_followup", "question", "answer", "feedback",
        # "notification", "post_task_notify", "other"}. Popped in _activate_and_run_inner
        # before _latest_root_result is written; only whitelisted origins are persisted.
        self._root_activation_origin: dict[str, str] = {}

        # IDs of recently explicitly stopped/deleted organizations (short-lived set used to let
        # in-flight tool calls return a semantic error like "organization stopped, task cancelled"
        # instead of "organization not running").
        self._recently_stopped_orgs: dict[str, float] = {}

        self._started = False

        global _runtime_instance
        _runtime_instance = self

    def _get_org_semaphore(self, org_id: str) -> asyncio.Semaphore:
        """Get the org-level concurrency semaphore (limits simultaneously active nodes)."""
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

        # Release all pending command trackers to prevent send_command waiters from hanging
        for tracker in list(self._active_user_cmd.values()):
            if not tracker.completed.is_set():
                tracker.completed.set()
        self._active_user_cmd.clear()
        self._root_activation_origin.clear()

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
                f"Invalid state transition: {org.status.value} -> {target.value} "
                f"(allowed targets: {', '.join(s.value for s in valid) or 'none'})"
            )

    # ------------------------------------------------------------------
    # Organization lifecycle
    # ------------------------------------------------------------------

    async def start_org(self, org_id: str) -> Organization:
        """Start an organization, transitioning it to ACTIVE."""
        org = self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        # Idempotent: if already ACTIVE/RUNNING, return directly to avoid throwing
        # "Invalid state transition: active -> active" on double-click. Still blocks illegal jumps (e.g. ARCHIVED -> ACTIVE).
        if org.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
            return org

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

    def mark_org_stopped(self, org_id: str) -> None:
        """Mark the organization as "just stopped" so in-flight tool calls return a friendlier error.

        Records a timestamp; considered expired after 15 minutes, at which point a missing
        messenger falls back to the "organization not running" semantics (meaning not activated,
        not just stopped).
        """
        self._recently_stopped_orgs[org_id] = time.monotonic()
        # Cap collection size to avoid long-run memory leaks
        if len(self._recently_stopped_orgs) > 256:
            cutoff = time.monotonic() - 900
            self._recently_stopped_orgs = {
                k: v for k, v in self._recently_stopped_orgs.items() if v >= cutoff
            }

    def is_org_recently_stopped(self, org_id: str) -> bool:
        """Return whether the organization was explicitly stopped/deleted recently (within 15 minutes)."""
        ts = self._recently_stopped_orgs.get(org_id)
        if ts is None:
            return False
        if time.monotonic() - ts > 900:
            self._recently_stopped_orgs.pop(org_id, None)
            return False
        return True

    async def _cancel_busy_nodes(self, org: Organization, reason: str) -> None:
        """Proactively cancel the tasks of all non-idle nodes before the org is stopped/deleted.

        BUSY nodes go through cancel_node_task for cooperative cancellation so _activate_and_run
        has a chance to exit and clean up node state; WAITING/ERROR nodes (which cancel_node_task
        early-returns on) are forced to IDLE + mailbox cleared + a follow-up org:node_status
        broadcast, ensuring the frontend sees the final state.
        """
        messenger = self._messengers.get(org.id)
        for node in list(org.nodes):
            if node.status == NodeStatus.BUSY:
                try:
                    await self.cancel_node_task(org.id, node.id, reason=reason)
                except Exception as e:
                    logger.debug(
                        f"[OrgRuntime] cancel_node_task failed for {node.id}: {e}"
                    )
            elif node.status in (NodeStatus.WAITING, NodeStatus.ERROR):
                # cancel_node_task early-returns for non-BUSY nodes, so we clean up manually here;
                # otherwise these nodes would remain in WAITING/ERROR state after stop.
                try:
                    if messenger is not None:
                        messenger.clear_node_pending(node.id)
                except Exception as e:
                    logger.debug(
                        f"[OrgRuntime] clear_node_pending failed for {node.id}: {e}"
                    )
                try:
                    self._set_node_status(org, node, NodeStatus.IDLE, reason)
                    self._node_current_chain.pop(f"{org.id}:{node.id}", None)
                except Exception as e:
                    logger.debug(
                        f"[OrgRuntime] _set_node_status failed for {node.id}: {e}"
                    )
                try:
                    await self._broadcast_ws("org:node_status", {
                        "org_id": org.id, "node_id": node.id,
                        "status": "idle", "current_task": "",
                    })
                except Exception:
                    pass

    async def stop_org(self, org_id: str) -> Organization:
        """Stop an organization."""
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        # Idempotent: when already DORMANT, don't throw "Invalid state transition: dormant -> dormant";
        # instead do another safety-net cleanup (to prevent leftover busy nodes / background tasks / mailbox)
        # and re-broadcast status_change so the frontend has a chance to reset nodes cleanly.
        if org.status == OrgStatus.DORMANT:
            self.mark_org_stopped(org_id)
            try:
                await self._cancel_busy_nodes(org, reason="org_stopped(idempotent)")
            except Exception as e:
                logger.debug(f"[OrgRuntime] idempotent stop cancel_busy_nodes: {e}")
            try:
                await self._stop_org_services(org_id)
            except Exception as e:
                logger.debug(f"[OrgRuntime] idempotent stop _stop_org_services: {e}")
            try:
                await self._cancel_org_tasks(org_id)
            except Exception as e:
                logger.debug(f"[OrgRuntime] idempotent stop _cancel_org_tasks: {e}")
            await self._broadcast_ws("org:status_change", {
                "org_id": org_id, "status": "dormant"
            })
            return org

        self._check_transition(org, OrgStatus.DORMANT)

        # Mark stopped first so subsequent in-flight tool calls can distinguish "stopped" from "not running"
        self.mark_org_stopped(org_id)

        # First cooperatively cancel per-node tasks, then close services / force-cancel asyncio tasks,
        # so the logs no longer show the misleading "organization not running" error
        await self._cancel_busy_nodes(org, reason="org_stopped")
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

        # Mark stopped immediately to avoid in-flight tool calls seeing "organization not running" during delete
        self.mark_org_stopped(org_id)

        # 1. Graceful stop (best-effort) — first cancel_busy_nodes, then clean up services
        if org.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING, OrgStatus.PAUSED):
            try:
                await self.stop_org(org_id)
            except Exception as e:
                logger.warning(f"[OrgRuntime] stop_org before delete failed for {org_id}: {e}")
        else:
            # Dormant orgs should also cancel any leftover busy nodes / background tasks
            try:
                await self._cancel_busy_nodes(org, reason="org_deleted")
            except Exception as e:
                logger.debug(f"[OrgRuntime] cancel_busy_nodes before delete failed: {e}")

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
        # Idempotent: return directly if already PAUSED
        if org.status == OrgStatus.PAUSED:
            return org
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
        # Idempotent: return directly if already ACTIVE/RUNNING
        if org.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
            return org
        self._check_transition(org, OrgStatus.ACTIVE)
        org.status = OrgStatus.ACTIVE
        org.updated_at = _now_iso()
        self._manager.update(org_id, {"status": org.status.value})
        self._org_quota_failures.pop(org_id, None)
        self._suppress_post_hook.pop(org_id, None)
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
            tagged_content = f"[From {persona.label}] {content}"
        else:
            tagged_content = content

        self._suppress_post_hook.pop(org_id, None)

        if self._is_stop_intent(content):
            await self._soft_stop_org(org_id)
            result = await self._activate_and_run(
                org, target, tagged_content,
                chain_id=chain_id, activation_origin="user_command",
            )
            if chain_id and isinstance(result, dict):
                result["chain_id"] = chain_id
            return result

        # Event-driven command completion detection: create a UserCommandTracker for this command.
        # It register_chain's on org_delegate_task invocations from the root node and
        # unregister_chain's on _mark_chain_closed. When the first activation completes + all chains
        # closed + root IDLE + root inbox empty, tracker.completed is set.
        # A watchdog coroutine runs in parallel to handle the "truly stuck" case
        # (warning / safety-net soft-stop); it does not participate in completion detection.
        tracker_key = (org_id, target.id)
        prior = self._active_user_cmd.pop(tracker_key, None)
        if prior is not None and not prior.completed.is_set():
            # Very rare: two commands overlap on the same root. Mark the old one completed so its
            # watchdog / waiters exit and nothing hangs.
            prior.completed.set()

        tracker = UserCommandTracker(org_id, target.id)
        self._active_user_cmd[tracker_key] = tracker

        watchdog_task = asyncio.create_task(self._command_watchdog(tracker))

        try:
            result = await self._activate_and_run(
                org, target, tagged_content,
                chain_id=chain_id, activation_origin="user_command",
            )
            # Check the completion condition immediately after the root's first ReAct round (hits directly when no delegation)
            self._maybe_finalize_tracker(tracker)
            if not tracker.completed.is_set():
                await tracker.completed.wait()
        finally:
            self._active_user_cmd.pop(tracker_key, None)
            if not watchdog_task.done():
                watchdog_task.cancel()
            try:
                await watchdog_task
            except (asyncio.CancelledError, Exception):
                pass

        # Take _latest_root_result as the true "user-visible final result" — it is only written by
        # the user_command / task_delivered / delivery_followup origins, so intermediate states
        # (e.g. the CEO answering the CFO's budget question) do not pollute it.
        final_result = self._latest_root_result.pop(org_id, None)
        if final_result is None:
            # Safety net: tracker completed but _latest_root_result was not written due to filtering
            # or exception; fall back to the return value of the first _activate_and_run.
            if isinstance(result, dict):
                final_result = dict(result)
            else:
                final_result = {
                    "node_id": target.id,
                    "result": str(result) if result is not None else "",
                }

        if tracker.auto_stopped:
            final_result["stopped_by_watchdog"] = True
            final_result.setdefault(
                "warning",
                "The organization had no progress for a long time and was auto-paused; this is the partial result so far.",
            )

        if chain_id:
            final_result["chain_id"] = chain_id
        return final_result

    async def cancel_node_task(
        self,
        org_id: str,
        node_id: str,
        reason: str = "User cancelled task",
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

        # (c2) If the cancelled node happens to be the root of an in-flight command, set its tracker
        # as completed so send_command's waiters return immediately — avoids the deadlock where
        # "after the user cancels via UI, the HTTP request hangs in the background waiting for the
        # completed event".
        tracker = self._active_user_cmd.get((org_id, node_id))
        if tracker is not None and not tracker.completed.is_set():
            tracker.completed.set()

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
            persona_label = org.user_persona.label if org.user_persona else "the owner"

            prompt = (
                f"[Organization startup — Business mandate]\n\n"
                f"You are the {root.role_title} of \"{org.name}\"; the organization has just started.\n"
                f"{persona_label} has entrusted you with full responsibility for the following core business:\n\n"
                f"---\n{org.core_business.strip()}\n---\n\n"
                f"## What you need to do now\n\n"
                f"1. **Define a working strategy**: based on the core business goals, draft a concrete action plan and staged objectives\n"
                f"2. **Break down and delegate**: decompose the work into concrete tasks and use org_delegate_task to assign them to suitable subordinates\n"
                f"3. **Start executing**: do not wait for further instructions — begin advancing the highest-priority work immediately\n"
                f"4. **Record decisions**: write the working strategy, task assignments, and staged goals onto the blackboard (org_write_blackboard)\n\n"
                f"## Working principles\n\n"
                f"- You are the highest-ranking person in this organization; judge and act autonomously and continuously, without waiting for {persona_label} to issue every step\n"
                f"- {persona_label}'s instructions are directional adjustments and supplements; you fully own day-to-day decisions\n"
                f"- For major decisions or risks, record them on the blackboard; {persona_label} will see them when checking the org's status\n"
                f"- Review progress regularly, adjust strategy, and ensure continuous advancement toward the goal\n\n"
                f"Get to work."
            )

            self.get_event_store(org.id).emit(
                "auto_kickoff", "system",
                {"root_node": root.id, "core_business_len": len(org.core_business)},
            )

            await self._activate_and_run(
                org, root, prompt, activation_origin="auto_kickoff",
            )
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

    def is_chain_closed(self, org_id: str, chain_id: str | None) -> bool:
        """Return True if the given chain_id has been accepted/rejected/cancelled."""
        if not chain_id:
            return False
        bucket = self._closed_chains.get(org_id)
        return bool(bucket and chain_id in bucket)

    def _mark_chain_closed(self, org_id: str, chain_id: str) -> None:
        """Record that a chain has been closed (accept/reject/cancel)."""
        if not org_id or not chain_id:
            return
        bucket = self._closed_chains.get(org_id)
        if bucket is None:
            bucket = OrderedDict()
            self._closed_chains[org_id] = bucket
        if chain_id in bucket:
            bucket.move_to_end(chain_id)
        else:
            bucket[chain_id] = time.time()
            while len(bucket) > self._closed_chain_max_per_org:
                bucket.popitem(last=False)
        # Chain-close event -> notify all UserCommandTrackers for this org to remove this chain
        # and attempt finalize. If all chains closed + root IDLE + inbox empty, the command is complete.
        try:
            self._tracker_unregister_chain(org_id, chain_id)
        except Exception:
            logger.debug(
                "[OrgRuntime] tracker unregister_chain failed",
                exc_info=True,
            )

    def _cleanup_accepted_chain(
        self,
        org_id: str,
        chain_id: str,
        *,
        reason: str = "accepted",
        cascade_cancel_children: bool = True,
    ) -> None:
        """Uniformly clean up runtime state for a closed task chain.

        Triggered by: task accept / reject / cancel. It:
          1. Adds the chain to the ``_closed_chains`` blacklist (consulted by `_on_node_message`
             and the `org_*` tools for soft-block).
          2. Removes every node binding in ``_node_current_chain`` that points to this chain.
          3. Releases the messenger's task_affinity / delegation_depth (if not already released).
          4. Cascades: marks the chain's unfinished child tasks in ProjectStore as CANCELLED and
             broadcasts ``org:task_cancelled`` for UI sync.

        Pure local state cleanup — it **does not** touch messages already in the mailbox queue;
        that is handled by the soft gate inside `_on_node_message`.
        """
        try:
            self._mark_chain_closed(org_id, chain_id)
        except Exception as exc:
            logger.debug("mark_chain_closed failed: %s", exc)

        try:
            prefix = f"{org_id}:"
            to_remove = [
                k for k, v in list(self._node_current_chain.items())
                if k.startswith(prefix) and v == chain_id
            ]
            for k in to_remove:
                self._node_current_chain.pop(k, None)
        except Exception as exc:
            logger.debug("clear node_current_chain failed: %s", exc)

        try:
            self._chain_delegation_depth.pop(chain_id, None)
        except Exception:
            pass
        try:
            messenger = self.get_messenger(org_id)
            if messenger:
                messenger.release_task_affinity(chain_id)
        except Exception:
            pass

        if cascade_cancel_children:
            try:
                self._cancel_chain_children_in_store(org_id, chain_id, reason)
            except Exception as exc:
                logger.debug("cancel_chain_children_in_store failed: %s", exc)

        logger.info(
            "[OrgRuntime] chain %s closed (%s) cleaned up", chain_id, reason,
        )

    def _cancel_chain_children_in_store(
        self, org_id: str, chain_id: str, reason: str,
    ) -> None:
        """Mark unfinished child tasks rooted at this chain in ProjectStore as CANCELLED.

        Only affects child tasks with status TODO / IN_PROGRESS / DELIVERED; ACCEPTED ones are left alone.
        """
        from openakita.orgs.models import TaskStatus
        from openakita.orgs.project_store import ProjectStore

        store = ProjectStore(self._manager._org_dir(org_id))
        root = store.find_task_by_chain(chain_id)
        if not root:
            return
        all_tasks: list = []
        try:
            for proj in store.list_projects():
                if proj.id != root.project_id:
                    continue
                all_tasks.extend(list(proj.tasks or []))
        except Exception:
            return

        to_cancel_ids: set[str] = set()
        pending = [root.id]
        while pending:
            parent_id = pending.pop()
            for t in all_tasks:
                if t.parent_task_id == parent_id and t.id != root.id:
                    if t.status in (
                        TaskStatus.TODO,
                        TaskStatus.IN_PROGRESS,
                        TaskStatus.DELIVERED,
                    ):
                        to_cancel_ids.add(t.id)
                    pending.append(t.id)

        if not to_cancel_ids:
            return

        cancelled_chain_ids: list[str] = []
        for t in all_tasks:
            if t.id in to_cancel_ids:
                try:
                    store.update_task(
                        t.project_id, t.id,
                        {"status": TaskStatus.CANCELLED},
                    )
                    if t.chain_id:
                        cancelled_chain_ids.append(t.chain_id)
                        self._mark_chain_closed(org_id, t.chain_id)
                except Exception as exc:
                    logger.debug("cancel child task %s failed: %s", t.id, exc)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast_ws(
                "org:task_cancelled_cascade",
                {
                    "org_id": org_id,
                    "root_chain_id": chain_id,
                    "cancelled_chain_ids": cancelled_chain_ids,
                    "reason": reason,
                },
            ))
        except RuntimeError:
            pass
        except Exception:
            pass

    async def _activate_and_run(
        self, org: Organization, node: OrgNode, prompt: str,
        chain_id: str | None = None,
        *,
        activation_origin: str | None = None,
    ) -> dict:
        """Activate a node agent and run a task (with org-level concurrency limit).

        ``activation_origin`` tags *this* activation's source for root-node
        result filtering. See :pyattr:`_FINAL_RESULT_ORIGINS`. When ``None``
        (default) the tag stored in ``_root_activation_origin`` is consumed
        as a fallback (legacy path). Prefer passing explicitly.
        """
        if node.status == NodeStatus.FROZEN:
            return {"error": f"{node.role_title} is frozen and cannot execute tasks"}
        if node.status == NodeStatus.OFFLINE:
            return {"error": f"{node.role_title} is offline"}

        sem = self._get_org_semaphore(org.id)
        async with sem:
            return await self._activate_and_run_inner(
                org, node, prompt, chain_id,
                activation_origin=activation_origin,
            )

    async def _activate_and_run_inner(
        self, org: Organization, node: OrgNode, prompt: str,
        chain_id: str | None = None,
        *,
        activation_origin: str | None = None,
    ) -> dict:
        """Inner implementation of _activate_and_run (already protected by the org semaphore)."""
        if node.status == NodeStatus.FROZEN:
            return {"error": f"{node.role_title} is frozen and cannot execute tasks"}
        if node.status == NodeStatus.OFFLINE:
            return {"error": f"{node.role_title} is offline"}

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
        self._touch_trackers_for_org(org.id)
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

            # Distinguish the task's actual exit reason: normal/ask_user -> completed;
            # loop_terminated -> force-terminated by the Supervisor;
            # max_iterations -> exceeded max iterations;
            # verify_incomplete -> TaskVerify judged incomplete but retries exhausted
            exit_reason = "normal"
            re_engine = None
            try:
                re_engine = getattr(agent, "reasoning_engine", None)
                if re_engine is not None:
                    exit_reason = getattr(re_engine, "_last_exit_reason", "normal") or "normal"
            except Exception:
                exit_reason = "normal"
                re_engine = None

            is_normal = exit_reason in ("normal", "ask_user")
            is_terminated = exit_reason == "loop_terminated"
            is_failed = exit_reason in ("max_iterations", "verify_incomplete")

            status_reason = "task_completed" if is_normal else (
                "task_terminated" if is_terminated else "task_failed"
            )
            self._set_node_status(org, node, NodeStatus.IDLE, status_reason)
            if is_normal:
                org.total_tasks_completed += 1
                self._node_consecutive_failures.pop(f"{org.id}:{node.id}", None)
                self._org_quota_failures.pop(org.id, None)
            await self._save_org(org)
            self._heartbeat.record_activity(org.id)

            if org.id not in self._active_orgs:
                return {"node_id": node.id, "result": result_text}

            # On non-normal exit, call failure_diagnoser to generate a "why-it-failed + suggestions" human-readable payload;
            # the normal-exit path skips this to avoid unnecessary work on the hot path.
            diagnosis: dict | None = None
            if not is_normal:
                try:
                    react_trace = getattr(re_engine, "_last_react_trace", None) if re_engine else None
                    diagnosis = _diagnose_failure(react_trace, exit_reason)
                except Exception as diag_err:
                    logger.debug(f"[OrgRuntime] failure diagnosis failed on {node.id}: {diag_err}")
                    diagnosis = None
                # Append the human-readable summary to result_text so even frontends showing only the chat bubble see the conclusion
                if diagnosis:
                    try:
                        human_summary = format_human_summary(diagnosis)
                        if human_summary and human_summary not in (result_text or ""):
                            separator = "\n\n" if result_text else ""
                            result_text = (result_text or "") + separator + human_summary
                    except Exception as fmt_err:
                        logger.debug(f"[OrgRuntime] format_human_summary failed: {fmt_err}")

            # Choose an event name matching exit_reason so the frontend can distinguish UI styles
            if is_normal:
                event_name = "task_completed"
                ws_event = "org:task_complete"
            elif is_terminated:
                event_name = "task_terminated"
                ws_event = "org:task_terminated"
            else:
                event_name = "task_failed"
                ws_event = "org:task_failed"

            event_payload: dict = {
                "result_preview": result_text[:_LIM_EVENT] if result_text else "",
                "exit_reason": exit_reason,
            }
            if diagnosis:
                event_payload["diagnosis"] = diagnosis
            self.get_event_store(org.id).emit(event_name, node.id, event_payload)

            await self._broadcast_ws("org:node_status", {
                "org_id": org.id, "node_id": node.id, "status": "idle",
                "current_task": "",
                "exit_reason": exit_reason,
            })
            ws_payload: dict = {
                "org_id": org.id, "node_id": node.id,
                "result_preview": result_text[:_LIM_WS] if result_text else "",
                "exit_reason": exit_reason,
            }
            if diagnosis:
                ws_payload["diagnosis"] = diagnosis
            await self._broadcast_ws(ws_event, ws_payload)
            if not is_normal:
                root_cause_tag = (diagnosis or {}).get("root_cause", "unknown")
                logger.warning(
                    f"[OrgRuntime] Node {node.id} ended with exit_reason={exit_reason}, "
                    f"root_cause={root_cause_tag}, emitting {event_name} (NOT task_completed)"
                )

            is_root = (node.level == 0 or not org.get_parent(node.id))
            if is_root:
                # Only activations whose origin is a "user-visible final state" may write _latest_root_result.
                # See _origin_from_msg_type and _FINAL_RESULT_ORIGINS:
                # - user_command: first activation dispatched by send_command
                # - task_delivered: subordinate submitted a deliverable, waking root for a combined report
                # - delivery_followup: post-acceptance follow-up handling
                # Inter-agent communication such as QUESTION/ANSWER/FEEDBACK/NOTIFICATION does not write,
                # to prevent the CEO's reply to a subordinate's question from being returned to the user as the "final result".
                # Prefer the explicitly passed activation_origin; if not provided (legacy path),
                # fall back to reading _root_activation_origin. Default conservatively to "user_command"
                # to preserve the behavior of historical call sites that do not track commands.
                if activation_origin:
                    origin = activation_origin
                else:
                    origin = self._pop_root_origin(
                        org.id, node.id, "user_command",
                    )
                if is_normal and origin in self._FINAL_RESULT_ORIGINS:
                    self._latest_root_result[org.id] = {
                        "node_id": node.id,
                        "result": result_text,
                        "origin": origin,
                    }

            # On non-normal exit, do not fire the post-task hook (to avoid passing "partial/failed results" downstream again)
            if is_normal:
                asyncio.ensure_future(self._post_task_hook(org, node))

            return_payload: dict = {
                "node_id": node.id,
                "result": result_text,
                "exit_reason": exit_reason,
            }
            if diagnosis:
                return_payload["diagnosis"] = diagnosis
            return return_payload

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
                        f"Failed {self._node_consecutive_failures[fail_key]} times in a row; auto-frozen"
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
                title="API quota exhausted; organization auto-paused",
                body=(
                    "All configured AI model endpoints are unusable due to insufficient balance or auth failure. "
                    "The organization has been auto-paused to avoid repeated failures. "
                    "Please top up on the respective platforms, then click 'Resume' on the organization panel to continue."
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
                "message": "API quota exhausted; organization auto-paused. Please top up and resume.",
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
            return "(Task cancelled)"
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

        per_node_tools = build_org_node_tools(org, node)
        per_node_by_name: dict[str, dict] = {t["name"]: t for t in per_node_tools}

        if hasattr(agent, "tool_catalog"):
            for tool_def in per_node_tools:
                agent.tool_catalog.add_tool(tool_def)
            if "org_delegate_task" not in per_node_by_name:
                agent.tool_catalog.remove_tool("org_delegate_task")
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
                if not name:
                    continue
                if name in per_node_by_name:
                    if name not in seen:
                        seen.add(name)
                        filtered.append(per_node_by_name[name])
                    continue
                if name.startswith("org_"):
                    continue
                if (name in _KEEP or name in allowed_external) and name not in seen:
                    seen.add(name)
                    filtered.append(t)
            for name, tool in per_node_by_name.items():
                if name not in seen:
                    seen.add(name)
                    filtered.append(tool)
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

        org_section = "\n".join(org_tool_lines) if org_tool_lines else "(none)"
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
            f"## Runtime environment\n"
            f"- Current time: {current_time}\n"
            f"- OS: {platform.system()} {platform.release()}\n"
            f"- Working directory: {workspace or os.getcwd()}\n"
            f"- Shell: {shell_type}"
        )
        if platform.system() == "Windows" and has_external:
            runtime_section += (
                "\n- Shell note: on Windows, for complex text processing prefer write_file to save a Python script,"
                " then run_shell python xxx.py to execute it — avoids PowerShell escaping issues."
            )
        parts.append(runtime_section)

        parts.append(f"## Organization collaboration tools (org_*)\n\n{org_section}")

        if has_external:
            ext_section = "\n".join(ext_tool_lines)
            parts.append(f"## External execution tools\n\n{ext_section}")

        parts.append(
            "Parameters marked * are required. Use get_tool_info(tool_name) to view a tool's full parameters."
        )

        rule_delivery = (
            "6. **On completion, reply directly**. You are the top-level owner; when work is done, summarize results in your reply — do not use org_submit_deliverable.\n"
            if is_root else
            "6. **Task delivery flow**. After completing assigned work, use org_submit_deliverable to submit to the delegator for acceptance. If rejected, revise and resubmit.\n"
        )

        if has_external:
            parts.append(
                "## Behavior rules\n\n"
                "1. **Use org_* tools for collaboration, external tools for execution**. Talk, delegate, and report via org_* tools; "
                "search information, write files, make plans, and other actual execution via external tools.\n"
                "2. **Share execution results**. Write important results obtained via external tools to the blackboard with org_write_blackboard for colleagues to read.\n"
                "3. **Reply concisely**. After calling a tool, summarize the result in 1-2 sentences.\n"
                "4. **Check before acting**. Use org_find_colleague when unsure who to contact; use org_search_policy when unsure about a process.\n"
                "5. **Avoid duplicate writes**. Before writing to the blackboard, use org_read_blackboard to check for similar content.\n"
                + rule_delivery +
                "7. **Request missing tools**. If a task needs a tool you don't have, use org_request_tools to request it from a superior."
            )
        else:
            parts.append(
                "## Behavior rules\n\n"
                "1. **Use only the org_* tools above**. Do not call non-org tools like write_file, read_file, run_shell — they are unavailable.\n"
                "2. **Reply concisely**. After calling a tool, summarize the result in 1-2 sentences.\n"
                "3. **Check before acting**. Use org_find_colleague when unsure who to contact; use org_search_policy when unsure about a process.\n"
                "4. **Record important information on the blackboard**. Use org_write_blackboard to record decisions, plans, progress for colleagues.\n"
                "5. **Avoid duplicate writes**. Before writing to the blackboard, use org_read_blackboard to check for similar content.\n"
                + rule_delivery +
                "7. **Request missing tools**. If a task needs a tool you don't have, use org_request_tools to request it from a superior."
            )

        # Core policy guardrails
        parts.append(
            "## Core policy red lines\n"
            "- Do not fabricate information. When uncertain, say so explicitly — do not invent data or results.\n"
            "- Do not pretend to execute. Without the corresponding tool, do not claim an operation was completed.\n"
            "- Do not perform harmful actions. Do not delete user data (unless explicitly requested) and do not access sensitive system paths."
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

        if self._suppress_post_hook.get(org_id):
            return

        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            return
        node = org.get_node(node_id)
        if not node or node.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
            return

        # ---------- Soft gate for closed task chains ----------
        # Purpose: after a task is accepted/rejected/cancelled, subsequent "non-assignment"
        # messages on that chain must not re-wake the agent's ReAct loop (this is one of
        # the main entry points for the user-reported "organization still assigns work on
        # its own after the task ends" issue).
        # Pass-through rules:
        #   - New TASK_ASSIGN (even if it carries an old chain_id, treated as a new assignment)
        #   - TASK_REJECTED (allow rework after rejection)
        # All other types (REPORT/ANSWER/QUESTION/TASK_DELIVERED/TASK_ACCEPTED/
        # FEEDBACK/NOTIFICATION ...) only broadcast a WebSocket event for UI visibility;
        # they do not restart ReAct.
        try:
            from openakita.config import settings as _settings
            suppress_on = getattr(
                _settings, "org_suppress_closed_chain_reactivation", True
            )
        except Exception:
            suppress_on = True

        if suppress_on:
            chain_id_peek = msg.metadata.get("task_chain_id") if msg.metadata else None
            closed = (
                bool(msg.metadata and msg.metadata.get("chain_closed"))
                or self.is_chain_closed(org_id, chain_id_peek)
            )
            if closed and msg.msg_type not in (
                MsgType.TASK_ASSIGN,
                MsgType.TASK_REJECTED,
            ):
                logger.info(
                    "[OrgRuntime] gate: skip ReAct activation for closed chain=%s "
                    "msg_type=%s from=%s to=%s",
                    chain_id_peek, msg.msg_type.value if hasattr(msg.msg_type, "value") else msg.msg_type,
                    msg.from_node, msg.to_node,
                )
                try:
                    await self._broadcast_ws("org:chain_closed_msg", {
                        "org_id": org_id,
                        "chain_id": chain_id_peek,
                        "from_node": msg.from_node,
                        "to_node": msg.to_node,
                        "msg_type": (
                            msg.msg_type.value if hasattr(msg.msg_type, "value")
                            else str(msg.msg_type)
                        ),
                        "content_preview": (msg.content or "")[:_LIM_WS],
                    })
                except Exception:
                    pass
                messenger = self.get_messenger(org_id)
                if messenger:
                    mb = messenger.get_mailbox(node_id)
                    if mb and not mb.is_paused:
                        mb.mark_handler_processed(msg.id)
                    try:
                        messenger.mark_processed(msg.id)
                    except Exception:
                        pass
                return
        # ---------- End of soft gate ----------

        active_count = self._node_active_count(org_id, node_id)

        messenger = self.get_messenger(org_id)
        pending = messenger.get_pending_count(node_id) if messenger else 0

        def _mark_dispatched() -> None:
            if messenger:
                mb = messenger.get_mailbox(node_id)
                if mb and not mb.is_paused:
                    mb.mark_handler_processed(msg.id)

        # Any dispatched message counts as a "the organization is breathing" progress signal.
        # Placed before the message is actually delivered to the agent, so it also covers the path
        # where "message received but the current node is at concurrency limit, message lingers in mailbox".
        self._touch_trackers_for_org(org_id)

        # Compute the origin tag for "this activation" based on message type. Passed explicitly to
        # _activate_and_run to avoid races where concurrent late messages overwrite a shared dict.
        msg_origin = self._origin_from_msg_type(msg.msg_type)

        if active_count >= self.max_concurrent_per_node:
            target_clone = self._try_route_to_clone(org, node, msg, pending)
            if target_clone:
                _mark_dispatched()
                task_prompt = self._format_incoming_message(msg)
                chain_id = msg.metadata.get("task_chain_id") or None
                await self._activate_and_run(
                    org, target_clone, task_prompt,
                    chain_id=chain_id, activation_origin=msg_origin,
                )
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
                    await self._activate_and_run(
                        org, new_clone, task_prompt,
                        chain_id=chain_id, activation_origin=msg_origin,
                    )
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
        await self._activate_and_run(
            org, node, task_prompt,
            chain_id=chain_id or None, activation_origin=msg_origin,
        )
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
            MsgType.TASK_ASSIGN: "Task received",
            MsgType.TASK_RESULT: "Task result received",
            MsgType.TASK_DELIVERED: "Task deliverable received",
            MsgType.TASK_ACCEPTED: "Task accepted",
            MsgType.TASK_REJECTED: "Task rejected",
            MsgType.REPORT: "Report received",
            MsgType.QUESTION: "Question received",
            MsgType.ANSWER: "Answer received",
            MsgType.ESCALATE: "Escalation received",
            MsgType.BROADCAST: "Organization broadcast received",
            MsgType.DEPT_BROADCAST: "Department broadcast received",
            MsgType.FEEDBACK: "Feedback received",
            MsgType.HANDSHAKE: "Handshake request received",
        }
        label = type_labels.get(msg.msg_type, "Message received")
        prefix = f"[{label}] from {msg.from_node}"
        if msg.reply_to:
            prefix += f" (reply to message {msg.reply_to})"

        chain_id = msg.metadata.get("task_chain_id", "")
        if chain_id:
            prefix += f" [Task chain: {chain_id[:12]}]"

        extra = ""
        if msg.msg_type == MsgType.TASK_DELIVERED:
            deliverable = msg.metadata.get("deliverable", "")
            summary = msg.metadata.get("summary", "")
            if deliverable:
                extra = f"\nDeliverable: {deliverable}"
            if summary:
                extra += f"\nWork summary: {summary}"
            extra += "\nPlease use org_accept_deliverable or org_reject_deliverable to review it."
        elif msg.msg_type == MsgType.TASK_REJECTED:
            reason = msg.metadata.get("rejection_reason", "")
            if reason:
                extra = f"\nRejection reason: {reason}\nPlease revise based on feedback and resubmit with org_submit_deliverable."
        elif msg.msg_type == MsgType.TASK_ASSIGN:
            if chain_id:
                extra = f"\nWhen finished, submit the deliverable via org_submit_deliverable with task_chain_id={chain_id}"
            else:
                extra = "\nWhen finished, submit the deliverable via org_submit_deliverable."

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
        # Any node status transition counts as a "the organization is breathing" progress signal,
        # which resets the command watchdog timer. Additionally, when a node enters IDLE, the
        # completion condition may be met, so trigger a finalize check (very cheap; O(0) when
        # there is no matching entry in _active_user_cmd).
        self._touch_trackers_for_org(org.id)
        if new_status == NodeStatus.IDLE:
            self._maybe_finalize_trackers_for_org(org.id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _activate_org(self, org: Organization) -> None:
        """Set up runtime infrastructure for an organization."""
        # When re-activating the same org, clear the "recently stopped" marker so that
        # new-session tool calls are not misclassified as "organization stopped"
        self._recently_stopped_orgs.pop(org.id, None)
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
                title="Deadlock detected",
                body=f"Circular wait detected between the following nodes: {cycles}",
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
        self._suppress_post_hook.pop(org_id, None)
        self._post_hook_cooldown = {
            k: v for k, v in self._post_hook_cooldown.items()
            if not k.startswith(f"{org_id}:")
        }

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

        # When an organization is deregistered, release all pending command trackers and origin
        # tags to avoid send_command's `await tracker.completed.wait()` hanging forever.
        for key in list(self._active_user_cmd.keys()):
            if key[0] == org_id:
                tracker = self._active_user_cmd.pop(key, None)
                if tracker and not tracker.completed.is_set():
                    tracker.completed.set()
        for k in list(self._root_activation_origin.keys()):
            if k.startswith(f"{org_id}:"):
                self._root_activation_origin.pop(k, None)

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
        if self._suppress_post_hook.get(org.id):
            return 0
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

        try:
            from openakita.config import settings as _settings
            suppress_on = getattr(
                _settings, "org_suppress_closed_chain_reactivation", True
            )
        except Exception:
            suppress_on = True

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

            # Same as the soft gate in `_on_node_message`: non-assignment messages on a closed
            # chain no longer activate ReAct; just mark them processed and let them "disappear
            # naturally" from the queue. Otherwise the drain path would bypass the handler gate.
            if suppress_on:
                chain_peek = msg.metadata.get("task_chain_id") if msg.metadata else None
                closed = (
                    bool(msg.metadata and msg.metadata.get("chain_closed"))
                    or self.is_chain_closed(org.id, chain_peek)
                )
                if closed and msg.msg_type not in (
                    MsgType.TASK_ASSIGN,
                    MsgType.TASK_REJECTED,
                ):
                    logger.info(
                        "[OrgRuntime] drain-gate skip closed chain=%s msg=%s",
                        chain_peek, msg.id,
                    )
                    continue

            logger.info(
                f"[OrgRuntime] Draining pending message {msg.id} for {node.id} "
                f"(remaining: {mailbox.pending_count})"
            )
            task_prompt = self._format_incoming_message(msg)
            chain_id = msg.metadata.get("task_chain_id") or None
            msg_origin = self._origin_from_msg_type(msg.msg_type)
            await self._activate_and_run(
                org, node, task_prompt,
                chain_id=chain_id, activation_origin=msg_origin,
            )
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

            # "Auto-notify parent on task completion" is disabled by default — this is one of
            # the main sources of the user-reported "organization runs autonomously for no reason"
            # issue: after a child node finishes, the runtime proactively wakes the parent for
            # ReAct, and the parent's LLM often ignores the "do not assign new work" prompt
            # instruction and keeps assigning more tasks.
            #
            # The drain path above (`_drain_node_pending(parent)`) is preserved — if the parent
            # really has a pending deliverable/question, it will still be handled.
            # To keep the historical behavior, set org_post_task_notify_parent to True.
            try:
                from openakita.config import settings as _settings
                notify_parent = bool(getattr(
                    _settings, "org_post_task_notify_parent", False,
                ))
            except Exception:
                notify_parent = False

            if not notify_parent:
                return

            cooldown_key = f"{org.id}:{parent.id}"
            now = time.monotonic()
            last = self._post_hook_cooldown.get(cooldown_key, 0)
            if now - last < 15:
                return
            self._post_hook_cooldown[cooldown_key] = now

            role_title = node.role_title or node.id
            prompt = (
                f"[Notice] {role_title} has completed a task.\n"
                f"If there is a deliverable awaiting acceptance, please handle it. Otherwise, no action is required.\n"
                f"⚠️ Prohibited: do not assign new tasks, do not expand the work scope, do not proactively start any new work."
            )
            # post_task_notify is not part of the "user command path" and must not write
            # _latest_root_result; otherwise the parent's handling of this notification would
            # pollute the user-visible final result.
            await self._activate_and_run(
                org, parent, prompt, activation_origin="post_task_notify",
            )
        except Exception as e:
            logger.debug(f"[OrgRuntime] Post-task hook error: {e}")

    _STOP_KEYWORDS = frozenset({
        "暂停", "停止", "取消", "别做了", "先不做", "到此为止",
        "不要继续", "停下来", "先暂停", "不用做了", "够了",
        "终止", "中止", "全部停止", "不做了",
        "pause", "stop", "cancel", "halt", "abort", "stop it",
        "don't continue", "quit", "enough", "no more",
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
            if node.status == NodeStatus.BUSY:
                try:
                    await self.cancel_node_task(org_id, node.id)
                except Exception:
                    pass
            elif node.status in (NodeStatus.WAITING, NodeStatus.ERROR):
                self._set_node_status(org, node, NodeStatus.IDLE, "soft_stop")
            if messenger:
                messenger.clear_node_pending(node.id)
        self.get_event_store(org_id).emit("soft_stop", "user", {})

    def _has_active_delegations(self, org_id: str, root_node_id: str) -> bool:
        """Return True if any downstream work exists for this command.

        Includes:
          - non-root nodes in BUSY/WAITING state
          - non-root nodes with pending messages in their mailbox
          - **root node itself** with pending messages (covers the window where
            a subordinate just submitted a deliverable but the TASK_DELIVERED
            message has not yet been dispatched to the root's ReAct loop).
        """
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
            if messenger.get_pending_count(root_node_id) > 0:
                return True
        return False

    # ------------------------------------------------------------------
    # UserCommandTracker helpers
    # ------------------------------------------------------------------

    def _get_tracker(
        self, org_id: str, root_node_id: str,
    ) -> UserCommandTracker | None:
        return self._active_user_cmd.get((org_id, root_node_id))

    def _trackers_for_org(self, org_id: str) -> list[UserCommandTracker]:
        """Return all active trackers for an organization (usually 0 or 1)."""
        return [
            t for (oid, _nid), t in self._active_user_cmd.items()
            if oid == org_id
        ]

    def _touch_trackers_for_org(self, org_id: str) -> None:
        """Refresh progress timestamp on every tracker active in this org.

        Called on any progress signal: node status change, org_* tool call,
        messenger dispatch, chain register/unregister. Cheap no-op when no
        command is in flight.
        """
        if not self._active_user_cmd:
            return
        for tracker in self._trackers_for_org(org_id):
            tracker._touch()

    def _maybe_finalize_tracker(
        self, tracker: UserCommandTracker,
    ) -> None:
        """If completion conditions are met, set tracker.completed.

        Completion = all chains closed + root IDLE + no active delegations
        (which already covers root/non-root pending messages).
        """
        if tracker.completed.is_set():
            return
        if tracker.open_chains:
            return
        org = self.get_org(tracker.org_id)
        if not org:
            tracker.completed.set()
            return
        root = org.get_node(tracker.root_node_id)
        if not root or root.status != NodeStatus.IDLE:
            return
        if self._has_active_delegations(tracker.org_id, tracker.root_node_id):
            return
        tracker.completed.set()

    def _maybe_finalize_trackers_for_org(self, org_id: str) -> None:
        if not self._active_user_cmd:
            return
        for tracker in self._trackers_for_org(org_id):
            self._maybe_finalize_tracker(tracker)

    def _tracker_register_chain(
        self, org_id: str, opener_node_id: str, chain_id: str,
    ) -> None:
        """Hook point for tool_handler: register a newly opened chain.

        Only the tracker whose root matches either ``opener_node_id`` itself
        or the opener's ancestor root is updated — this covers both the case
        where the CEO(root) delegates directly, and the case where a
        subordinate delegates further (the chain still belongs to the current
        user command).
        """
        if not self._active_user_cmd or not chain_id:
            return
        org = self.get_org(org_id)
        if not org:
            return
        for tracker in self._trackers_for_org(org_id):
            if tracker.root_node_id == opener_node_id or self._is_descendant(
                org, tracker.root_node_id, opener_node_id,
            ):
                tracker.register_chain(chain_id)

    def _tracker_unregister_chain(
        self, org_id: str, chain_id: str,
    ) -> None:
        if not self._active_user_cmd or not chain_id:
            return
        for tracker in self._trackers_for_org(org_id):
            if chain_id in tracker.open_chains:
                tracker.unregister_chain(chain_id)
        self._maybe_finalize_trackers_for_org(org_id)

    @staticmethod
    def _is_descendant(
        org: Organization, ancestor_id: str, node_id: str,
    ) -> bool:
        """Return True if ``node_id`` is a descendant of (or equal to) ``ancestor_id``."""
        if ancestor_id == node_id:
            return True
        current = org.get_node(node_id)
        # Walk up via get_parent; bounded by node count to avoid accidental cycles.
        seen: set[str] = set()
        depth = 0
        while current and depth < len(org.nodes) + 1:
            parent = org.get_parent(current.id)
            if not parent:
                return False
            if parent.id == ancestor_id:
                return True
            if parent.id in seen:
                return False
            seen.add(parent.id)
            current = parent
            depth += 1
        return False

    def _mark_root_origin(
        self, org_id: str, node_id: str, origin: str,
    ) -> None:
        """Tag the next `_activate_and_run_inner` for this root node with an origin.

        The tag is consumed (popped) inside `_activate_and_run_inner` and
        controls whether the resulting FINAL_ANSWER gets written into
        `_latest_root_result`. Only writes from whitelisted origins
        (user_command / task_delivered / delivery_followup) surface to the
        user; inter-agent question/answer replies are discarded to avoid
        polluting the final command result.
        """
        if not org_id or not node_id or not origin:
            return
        self._root_activation_origin[f"{org_id}:{node_id}"] = origin

    def _pop_root_origin(
        self, org_id: str, node_id: str, default: str = "user_command",
    ) -> str:
        return self._root_activation_origin.pop(
            f"{org_id}:{node_id}", default,
        )

    @staticmethod
    def _origin_from_msg_type(msg_type: Any) -> str:
        """Map an inbound message type to an activation origin tag."""
        value = getattr(msg_type, "value", msg_type)
        return {
            MsgType.TASK_ASSIGN.value: "task_assign",
            MsgType.TASK_DELIVERED.value: "task_delivered",
            MsgType.TASK_ACCEPTED.value: "delivery_followup",
            MsgType.TASK_REJECTED.value: "delivery_followup",
            MsgType.REPORT.value: "report",
            MsgType.QUESTION.value: "question",
            MsgType.ANSWER.value: "answer",
            MsgType.ESCALATE.value: "escalate",
            MsgType.BROADCAST.value: "broadcast",
            MsgType.DEPT_BROADCAST.value: "broadcast",
            MsgType.FEEDBACK.value: "feedback",
            MsgType.HANDSHAKE.value: "handshake",
        }.get(value, "other")

    _FINAL_RESULT_ORIGINS: frozenset[str] = frozenset({
        "user_command",
        "task_delivered",
        "delivery_followup",
    })

    async def _command_watchdog(self, tracker: UserCommandTracker) -> None:
        """Stuck-detection watchdog for a user command.

        Does **not** participate in completion judgement. Every iteration it
        checks ``time.monotonic() - tracker.last_progress_at``:
          - >= warn_secs and not yet warned → broadcast stuck warning, mark warned
          - >= autostop_secs → mark auto_stopped, soft_stop the org, set completed
          - wall-clock since start >= hard_cap → same soft_stop path

        Any progress signal calls ``tracker._touch()`` which resets
        ``last_progress_at`` and ``warned_stuck``, so long tasks that keep
        producing progress never trip the watchdog.
        """
        try:
            from openakita.config import settings as _settings
        except Exception:
            _settings = None

        def _cfg(attr: str, default: int) -> int:
            if _settings is None:
                return default
            try:
                v = int(getattr(_settings, attr, default) or default)
            except Exception:
                v = default
            return v

        warn_secs = max(30, _cfg("org_command_stuck_warn_secs", 300))
        autostop_secs = max(
            warn_secs + 60, _cfg("org_command_stuck_autostop_secs", 1800)
        )
        hard_cap = _cfg("org_command_timeout_secs", 10800)

        poll_interval = max(5.0, min(15.0, warn_secs / 3.0))

        try:
            while not tracker.completed.is_set():
                try:
                    await asyncio.wait_for(
                        tracker.completed.wait(), timeout=poll_interval,
                    )
                    return
                except asyncio.TimeoutError:
                    pass

                if tracker.completed.is_set():
                    return

                now = time.monotonic()
                idle = now - tracker.last_progress_at

                if idle >= warn_secs and not tracker.warned_stuck:
                    tracker.warned_stuck = True
                    try:
                        await self._broadcast_ws("org:command_stuck_warning", {
                            "org_id": tracker.org_id,
                            "root_node_id": tracker.root_node_id,
                            "command_id": tracker.command_id or "",
                            "open_chains": list(tracker.open_chains),
                            "idle_secs": int(idle),
                        })
                    except Exception:
                        logger.debug(
                            "[CmdWatchdog] broadcast stuck_warning failed",
                            exc_info=True,
                        )
                    logger.warning(
                        "[CmdWatchdog] org=%s root=%s idle=%ds (warn)",
                        tracker.org_id, tracker.root_node_id, int(idle),
                    )

                should_autostop = idle >= autostop_secs
                if (
                    not should_autostop
                    and hard_cap > 0
                    and (now - tracker.started_at) >= hard_cap
                ):
                    should_autostop = True
                    logger.warning(
                        "[CmdWatchdog] org=%s root=%s hit hard cap %ds",
                        tracker.org_id, tracker.root_node_id, hard_cap,
                    )

                if should_autostop and not tracker.auto_stopped:
                    tracker.auto_stopped = True
                    logger.warning(
                        "[CmdWatchdog] org=%s root=%s auto soft-stopping (idle=%ds)",
                        tracker.org_id, tracker.root_node_id, int(idle),
                    )
                    try:
                        await self._soft_stop_org(tracker.org_id)
                    except Exception:
                        logger.error(
                            "[CmdWatchdog] soft_stop failed",
                            exc_info=True,
                        )
                    finally:
                        tracker.completed.set()
                    return
        except asyncio.CancelledError:
            return
        except Exception:
            logger.error(
                "[CmdWatchdog] unexpected error, exiting watchdog",
                exc_info=True,
            )

    async def _wait_delegation_completion(
        self, org_id: str, root_node_id: str, timeout: int = 300,
    ) -> dict | None:
        """Deprecated: legacy time-based waiter, kept only for back-compat.

        New code path in :meth:`send_command` uses UserCommandTracker +
        _command_watchdog for event-driven completion. This wrapper is
        retained so external callers (if any) keep functioning.
        """
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
            "stuck_busy": f"no activity in BUSY state for {stuck_secs} seconds",
            "error_not_recovering": "persistent ERROR state did not recover",
        }.get(reason, reason)
        msg = OrgMessage(
            org_id=org.id,
            from_node="system",
            to_node=parent.id,
            msg_type=MsgType.FEEDBACK,
            content=(
                f"[Watchdog notice] Your subordinate {node.role_title}({node.id}) "
                f"was auto-recovered because [{reason_text}]. "
                f"The node has been reset to idle and its previous task was interrupted. "
                f"If any delegated task is unfinished, please reassign or follow up."
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
                        if self._suppress_post_hook.get(org_id):
                            continue
                        roots = org.get_root_nodes()
                        if roots:
                            root = roots[0]
                            if root.status == NodeStatus.IDLE:
                                prompt = (
                                    "[Watchdog kick] The organization has been silent for a long time. "
                                    "Please check the blackboard and current progress, "
                                    "and decide whether to push the work forward or assign a new task."
                                )
                                self._heartbeat.record_activity(org_id)
                                asyncio.ensure_future(
                                    self._activate_and_run(
                                        org, root, prompt,
                                        activation_origin="watchdog_kick",
                                    )
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
                                f"[Idle check] You have been idle for {int(idle_secs)} seconds.\n"
                                f"Please check the organization blackboard (org_read_blackboard) and see whether there is work to push forward.\n"
                                f"If there are unfinished goals, plan the next step. If everything is fine, briefly describe the current status."
                            )
                        else:
                            prompt = (
                                f"[Idle check] You have been idle for {int(idle_secs)} seconds.\n"
                                f"Please check whether there is pending work, or report your idle status to your superior to request new tasks."
                            )

                        node_last_probed[node.id] = now
                        node_thresholds[node.id] = min(threshold * 1.5, 600)
                        if self._suppress_post_hook.get(org_id):
                            continue
                        await self._activate_and_run(
                            org, node, prompt, activation_origin="idle_probe",
                        )
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
            if tool_name in ("write_file", "generate_image", "deliver_artifacts"):
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
        ".md": "Markdown document",
        ".txt": "Text file",
        ".csv": "CSV data",
        ".json": "JSON data",
        ".py": "Python script",
        ".js": "JavaScript script",
        ".html": "HTML page",
        ".pdf": "PDF document",
        ".png": "PNG image",
        ".jpg": "JPEG image",
        ".jpeg": "JPEG image",
        ".gif": "GIF image",
        ".webp": "WebP image",
        ".svg": "SVG graphic",
        ".xlsx": "Excel spreadsheet",
        ".docx": "Word document",
        ".zip": "Archive",
    }

    def _register_file_output(
        self,
        org_id: str,
        node_id: str,
        *,
        chain_id: str | None,
        filename: str | None,
        file_path: str | None,
        workspace: Path | None = None,
    ) -> dict | None:
        """Canonical entry for recording a file produced by an org node.

        This is the single place that:
          1. resolves a (possibly relative) file path against the org workspace
          2. writes a RESOURCE entry to the org blackboard
          3. broadcasts org:blackboard_update so the frontend shows the
             attachment chip in the chat panel
          4. links the attachment onto the current ProjectTask

        All other call sites (write_file / generate_image hook,
        org_submit_deliverable, deliver_artifacts hook) must funnel through
        this function — do NOT introduce a parallel registration path.

        Returns the registered attachment dict on success, or None if the
        file could not be resolved / does not exist / no blackboard available.
        """
        if not file_path:
            return None

        p = Path(file_path)
        if not p.is_absolute():
            base = workspace or Path.cwd()
            p = (base / p).resolve()
        else:
            p = p.resolve()

        if not p.exists() or not p.is_file():
            return None

        size_bytes = p.stat().st_size
        resolved_name = filename or p.name
        ext = p.suffix.lower()
        ext_label = self._FILE_EXT_LABELS.get(ext, "File")

        attachment = {
            "filename": resolved_name,
            "path": str(p),
            "size_bytes": size_bytes,
        }

        bb = self.get_blackboard(org_id)
        if not bb:
            return None

        entry = bb.write_org(
            content=f"📎 Produced {ext_label}: **{resolved_name}**\n📂 Path: `{str(p)}`",
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
                "filename": resolved_name,
                "file_path": str(p),
                "file_size": size_bytes,
            }))

        _TEXT_EXTS = {".md", ".txt", ".html", ".json", ".yaml", ".yml", ".csv", ".xml"}
        text_preview = ""
        if ext in _TEXT_EXTS and size_bytes < 50_000:
            try:
                text_preview = p.read_text(encoding="utf-8", errors="replace")[:3000]
            except Exception:
                pass

        content_for_task = f"📎 Produced file: **{resolved_name}**\n📂 Path: `{str(p)}`"
        if text_preview:
            content_for_task += (
                f"\n\n<details><summary>File content preview</summary>\n\n"
                f"{text_preview}\n\n</details>"
            )

        if chain_id:
            try:
                self._tool_handler._link_project_task(
                    org_id, chain_id,
                    deliverable_content=content_for_task,
                    file_attachment={
                        "filename": resolved_name,
                        "file_path": str(p),
                        "file_size": size_bytes,
                    },
                )
            except Exception:
                pass

        return {
            "filename": resolved_name,
            "file_path": str(p),
            "file_size": size_bytes,
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
        """Thin wrapper that extracts (filename, file_path) from a tool
        invocation and funnels into _register_file_output.

        Supports write_file / generate_image / deliver_artifacts. Any future
        producer should also go through this wrapper, not call
        _register_file_output directly with ad-hoc arguments.
        """
        import json as _json

        if tool_name == "write_file":
            if "❌" in result:
                return
            # LLMs frequently emit write_file with filename / filepath /
            # file_path instead of the canonical path. The tool implementation
            # (tools/handlers/filesystem.py::_write_file) also falls back to
            # these aliases, so we honour the same set here to keep the hook
            # aligned with whatever actually got written.
            file_path = (
                tool_input.get("path")
                or tool_input.get("filepath")
                or tool_input.get("file_path")
                or tool_input.get("filename")
                or ""
            )
            if not file_path:
                return
            chain_id = self.get_current_chain_id(org_id, node_id)
            self._register_file_output(
                org_id, node_id,
                chain_id=chain_id,
                filename=None,
                file_path=file_path,
                workspace=workspace,
            )
            return

        if tool_name == "generate_image":
            try:
                data = _json.loads(result)
                if not data.get("ok"):
                    return
                file_path = data.get("saved_to", "")
            except Exception:
                return
            if not file_path:
                return
            chain_id = self.get_current_chain_id(org_id, node_id)
            self._register_file_output(
                org_id, node_id,
                chain_id=chain_id,
                filename=None,
                file_path=file_path,
                workspace=workspace,
            )
            return

        if tool_name == "deliver_artifacts":
            # deliver_artifacts returns a JSON envelope with receipts. Desktop
            # mode receipts use status == "delivered" and include an absolute
            # "path". Register each delivered file so the chat UI shows the
            # attachment chip just like it does for write_file outputs.
            try:
                text = result or ""
                # Some code paths append "\n\n[执行日志]..." (execution log) after the JSON.
                if "\n\n[执行日志]" in text:
                    text = text[: text.index("\n\n[执行日志]")]
                data = _json.loads(text)
            except Exception:
                return
            if not isinstance(data, dict):
                return
            receipts = data.get("receipts") or []
            if not isinstance(receipts, list):
                return
            chain_id = self.get_current_chain_id(org_id, node_id)
            for r in receipts:
                if not isinstance(r, dict):
                    continue
                if r.get("status") != "delivered":
                    continue
                path = r.get("path") or r.get("file_path")
                if not path:
                    continue
                self._register_file_output(
                    org_id, node_id,
                    chain_id=chain_id,
                    filename=r.get("name") or r.get("filename"),
                    file_path=path,
                    workspace=workspace,
                )
            return
