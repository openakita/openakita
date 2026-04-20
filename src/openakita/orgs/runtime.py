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
      - no other nodes under this org are still BUSY/WAITING with pending work, AND
      - (when ``org_root_post_summary`` is enabled) the root has produced a
        post-summary ReAct after being woken up by the auto-pushed
        ``task_complete`` notification.

    This tracker is event-driven: `register_chain`/`unregister_chain` are called
    from `_handle_org_delegate_task` and `_mark_chain_closed` respectively.
    Completion is signalled via :pyattr:`completed`.

    The :pyattr:`last_progress_at` timestamp is refreshed by `_touch()` whenever
    any progress signal fires (node status change, org tool call, messenger
    dispatch, chain event). It is consumed **only** by the command watchdog to
    decide whether to emit a stuck warning or to soft-stop the organization.
    It does **not** participate in completion judgement.

    State machine (when ``org_root_post_summary`` enabled):
      ``running`` → (subtree closed + root idle) → ``awaiting_summary``
      ``awaiting_summary`` → (root re-activated and back to idle) → ``done``
    When ``org_root_post_summary`` disabled the tracker behaves like before:
    once the subtree is closed and root is idle, ``completed`` is set directly
    (state stays ``running`` for compatibility).
    """

    __slots__ = (
        "org_id",
        "root_node_id",
        "command_id",
        "open_chains",
        "root_chain_id",
        "completed",
        "last_progress_at",
        "started_at",
        "warned_stuck",
        "auto_stopped",
        "state",
        "summary_pushed_at",
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
        # The first chain opened under this command (typically created by
        # the root node's first `org_delegate_task`). Used by the subtree
        # walker in ``_maybe_finalize_tracker`` as the root of the chain
        # tree. ``None`` when the root has not delegated anything yet.
        self.root_chain_id: str | None = None
        self.completed: asyncio.Event = asyncio.Event()
        now = time.monotonic()
        self.last_progress_at: float = now
        self.started_at: float = now
        self.warned_stuck: bool = False
        self.auto_stopped: bool = False
        # See class docstring for state machine details.
        self.state: str = "running"
        # monotonic time when summary inbox push happened (debounce).
        self.summary_pushed_at: float = 0.0

    def _touch(self) -> None:
        self.last_progress_at = time.monotonic()
        self.warned_stuck = False

    def register_chain(self, chain_id: str) -> None:
        if not chain_id:
            return
        self.open_chains.add(chain_id)
        if self.root_chain_id is None:
            self.root_chain_id = chain_id
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
        # 子链 → 父链 映射；由 `_handle_org_delegate_task` 在
        # `org_chain_parent_enforced=True` 时维护。tracker 据此沿向上指针
        # 遍历整棵 chain 子树，决定是否所有后代 chain 都已关闭。
        # key=chain_id, value=parent_chain_id 或 None（顶层 chain）。
        self._chain_parent: dict[str, str | None] = {}
        # chain 关闭事件：由 `_handle_org_delegate_task` 创建，
        # 由 `_mark_chain_closed` 在关链时 set，供 `org_wait_for_deliverable`
        # 工具阻塞等待。短期映射，超过 max_chain_events 后按 LRU 弹出（
        # 弹出前事件已经被 set，wait 任务已经收到通知，不会误等）。
        self._chain_events: OrderedDict[str, asyncio.Event] = OrderedDict()
        self._max_chain_events: int = 2048
        # 节点 inbox "新事件" 异步信号：sub-agent 发来 question/escalate 等
        # 需要 coordinator 立即处理的消息时被 set，用于 `org_wait_for_deliverable`
        # 跳出阻塞，避免 coordinator 阻塞导致的死锁。key=org_id:node_id。
        self._node_inbox_events: dict[str, asyncio.Event] = {}
        # 已验收/打回/取消的任务链集合（按组织维度）。用于：
        #   1) 抑制已关闭 chain 的消息重新唤醒 agent ReAct；
        #   2) 阻断对已关闭 chain 的 delegate/submit；
        #   3) 其它与 chain 生命周期相关的幂等判断。
        # 长度受限：每个 org 最多保留最近 N 个，防止长时间运行的组织集合膨胀。
        self._closed_chains: dict[str, "OrderedDict[str, float]"] = {}
        self._closed_chain_max_per_org: int = 512
        self.max_concurrent_per_node: int = 2
        self._idle_tasks: dict[str, asyncio.Task] = {}

        # ── Idle probe 状态（实例级，跨 IDLE/ACTIVE 切换持久） ──
        # 解决原实现把 thresholds 作为协程局部变量、节点 IDLE→ACTIVE→IDLE
        # 切换被 pop 清零导致"自适应增长"实际从未生效的问题。
        # 所有 key 形式统一为 f"{org_id}:{node_id}"。
        self._idle_node_thresholds: dict[str, float] = {}
        self._idle_node_last_probed: dict[str, float] = {}
        # 独立的"有效行动"时间戳：仅在节点成功 outbound（delegate/send_msg/
        # reply/submit/escalate）时由 tool_handler 写入。区别于 _node_last_activity
        # （后者在每次 tool call 都更新，包含 idle_probe 自己触发的活动）。
        self._node_last_effective: dict[str, float] = {}
        # 节点收到 inbound（task/message/reply/feedback）的时间戳。由 messenger 写入。
        self._node_last_inbound: dict[str, float] = {}
        # 上次 idle_probe 触发时记录的时间，用于下一轮判定是否产生有效行动。
        self._idle_probe_pending_since: dict[str, float] = {}
        # 节点累计"无效唤醒次数"。≥ _idle_max_ineffective 时永久暂停 probe，
        # 直到收到 inbound 才在 _on_inbound_for_node 重置。
        self._idle_node_ineffective: dict[str, int] = {}
        # 组织级"全员安静"起始时间。所有节点 IDLE + 无 in-progress chain +
        # 无 pending message + 无 active user command 的连续起始时间。
        self._idle_org_quiet_since: dict[str, float] = {}
        # Idle probe 调参（实例属性而非常量，方便测试覆盖）
        self._idle_base_threshold: float = 120.0
        self._idle_max_threshold: float = 600.0
        self._idle_max_ineffective: int = 2
        self._idle_org_quiet_grace: float = 300.0   # 5 min 后熔断
        self._idle_org_silent_interval: float = 1800.0  # 熔断后 30 min 心跳

        # 组织级并发控制：限制每个组织同时激活的节点数
        self.max_concurrent_nodes_per_org: int = 5
        self._org_semaphores: dict[str, asyncio.Semaphore] = {}

        self._save_locks: dict[str, asyncio.Lock] = {}

        self._node_consecutive_failures: dict[str, int] = {}
        self._org_quota_failures: dict[str, int] = {}

        self._post_hook_cooldown: dict[str, float] = {}
        self._suppress_post_hook: dict[str, bool] = {}
        self._latest_root_result: dict[str, dict] = {}

        # 用户命令生命周期追踪：key=(org_id, root_node_id) → UserCommandTracker
        # 由 send_command 在命令开始时创建、结束时移除。用于事件驱动的命令完成
        # 判定（所有 chain 关闭 + root IDLE + root inbox 空），并给看门狗提供
        # `last_progress_at` / `warned_stuck` 等状态。
        self._active_user_cmd: dict[tuple[str, str], UserCommandTracker] = {}

        # root 节点"下一次激活"的来源标签，控制 _latest_root_result 的写入门禁。
        # key = f"{org_id}:{node_id}"，value ∈ {"user_command", "task_delivered",
        # "delivery_followup", "question", "answer", "feedback",
        # "notification", "post_task_notify", "other"}。在 _activate_and_run_inner
        # 写入 _latest_root_result 前 pop 出来，只有在白名单内的来源才写入。
        self._root_activation_origin: dict[str, str] = {}

        # 最近被显式停止/删除的组织 id（短期集合，用于让 in-flight tool 调用
        # 返回"组织已停止，任务被取消"这样的语义化错误而不是"组织未运行"）
        self._recently_stopped_orgs: dict[str, float] = {}

        # 失败/终止诊断卡片去重：在 _diagnosis_emit_window_secs 窗口内，
        # 同一 (org_id, node_id, root_cause) 只 broadcast 一次，避免 verify_incomplete
        # 反复重试或 watchdog 多次 emit 导致前端聊天气泡里出现多张相同的失败卡片。
        # key = (org_id, node_id, root_cause), value = 上次 emit 的时间戳（秒）。
        self._recent_diagnosis_emit: dict[tuple[str, str, str], float] = {}
        self._diagnosis_emit_window_secs: float = 30.0

        # 工具级在途锁：防止 LLM 在同一 ReAct iter 内 emit 多个相同 tool_use
        # （如 3 次 org_delegate_task 给同一 to_node 同一 chain）造成下游
        # mailbox 重复入队。key = "{tool}:{org_id}:{node_id}:{...}"，
        # value = 抢到锁的时间戳（秒）。窗口外旧 key 可被覆盖。
        self._tool_inflight_keys: dict[str, float] = {}
        self._tool_inflight_window_secs: float = 5.0

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

    def _should_skip_diagnosis_emit(
        self, org_id: str, node_id: str, root_cause: str
    ) -> bool:
        """判定 (org, node, root_cause) 在去重窗口内是否已 emit 过失败卡片。

        命中（应跳过）时仅刷新时间戳并返回 True；未命中（应 emit）时记录
        时间戳并返回 False。窗口外的旧条目顺手清理，避免长时间运行内存累积。
        """
        import time as _t
        now = _t.time()
        key = (org_id, node_id, root_cause or "unknown")
        last = self._recent_diagnosis_emit.get(key)
        if last is not None and now - last < self._diagnosis_emit_window_secs:
            self._recent_diagnosis_emit[key] = now
            return True
        self._recent_diagnosis_emit[key] = now
        if len(self._recent_diagnosis_emit) > 4096:
            cutoff = now - self._diagnosis_emit_window_secs * 4
            stale = [k for k, ts in self._recent_diagnosis_emit.items() if ts < cutoff]
            for k in stale:
                self._recent_diagnosis_emit.pop(k, None)
        return False

    def _try_acquire_tool_inflight(self, key: str) -> bool:
        """尝试获取工具在途锁。窗口内同 key 已被抢占则返回 False。

        返回 True 表示当前调用是窗口内首次，调用方在 messenger 真正成功后
        必须调用 :py:meth:`_release_tool_inflight` 释放（或不释放等待自然过期）。
        """
        import time as _t
        now = _t.time()
        last = self._tool_inflight_keys.get(key)
        if last is not None and now - last < self._tool_inflight_window_secs:
            return False
        self._tool_inflight_keys[key] = now
        if len(self._tool_inflight_keys) > 4096:
            cutoff = now - self._tool_inflight_window_secs * 4
            stale = [k for k, ts in self._tool_inflight_keys.items() if ts < cutoff]
            for k in stale:
                self._tool_inflight_keys.pop(k, None)
        return True

    def _release_tool_inflight(self, key: str) -> None:
        """释放工具在途锁；幂等。"""
        self._tool_inflight_keys.pop(key, None)

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

        # 释放所有挂起的命令 tracker，防止 send_command 的等待者悬挂
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
        self._idle_node_thresholds.clear()
        self._idle_node_last_probed.clear()
        self._node_last_effective.clear()
        self._node_last_inbound.clear()
        self._idle_probe_pending_since.clear()
        self._idle_node_ineffective.clear()
        self._idle_org_quiet_since.clear()

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

        # 幂等：已经是 ACTIVE/RUNNING 就直接返回，避免双击启动按钮抛
        # "无效状态转换: active -> active"。仍然拦住非法跳跃（如 ARCHIVED -> ACTIVE）。
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
        """把组织标记为"刚被停止"，让 in-flight 工具调用返回更友好的错误。

        记录一个时间戳；15 分钟后被认为已过期，此时仍找不到 messenger
        可回到"组织未运行"的语义（说明是未激活，不是被停）。
        """
        self._recently_stopped_orgs[org_id] = time.monotonic()
        # 限制集合大小，避免长时间运行后内存泄漏
        if len(self._recently_stopped_orgs) > 256:
            cutoff = time.monotonic() - 900
            self._recently_stopped_orgs = {
                k: v for k, v in self._recently_stopped_orgs.items() if v >= cutoff
            }

    def is_org_recently_stopped(self, org_id: str) -> bool:
        """返回组织是否在近期（15 分钟内）被显式 stop/delete。"""
        ts = self._recently_stopped_orgs.get(org_id)
        if ts is None:
            return False
        if time.monotonic() - ts > 900:
            self._recently_stopped_orgs.pop(org_id, None)
            return False
        return True

    async def _cancel_busy_nodes(self, org: Organization, reason: str) -> None:
        """在组织停止/删除前，主动取消所有未空闲节点的任务。

        BUSY 节点走 cancel_node_task 触发协作式取消，让 _activate_and_run
        有机会退出并清理 node 状态；WAITING/ERROR 节点（cancel_node_task
        早返回不处理）则在这里直接强制 IDLE + 清 mailbox + 补一次
        org:node_status 广播，确保前端拿到最终状态。
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
                # cancel_node_task 对非 BUSY 节点直接早返回，这里手动收尾，
                # 否则 stop 之后这些节点还会留着 WAITING/ERROR 状态。
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

        # 幂等：已经 DORMANT 时不再抛 "无效状态转换: dormant -> dormant"，
        # 而是再做一次兜底清理（防止之前残留的 busy 节点 / 后台任务 / mailbox），
        # 并补播一次 status_change，让前端有机会把节点重置干净。
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

        # 先标记停止状态，让后续 in-flight tool call 能区分"停止"与"未运行"
        self.mark_org_stopped(org_id)

        # 先协作取消各节点任务，再关闭服务 / 强制取消 asyncio tasks，
        # 这样日志里不会再出现"组织未运行"的误导性错误
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

        # 立即标记停止，避免删除期间 in-flight 工具调用看到"组织未运行"
        self.mark_org_stopped(org_id)

        # 1. Graceful stop (best-effort) —— 会先 cancel_busy_nodes 再清理服务
        if org.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING, OrgStatus.PAUSED):
            try:
                await self.stop_org(org_id)
            except Exception as e:
                logger.warning(f"[OrgRuntime] stop_org before delete failed for {org_id}: {e}")
        else:
            # Dormant 组织也应取消任何遗留的 busy 节点 / 后台任务
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
        # 幂等：已经 PAUSED 直接返回
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
        # 幂等：已经 ACTIVE/RUNNING 直接返回
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
            tagged_content = f"[来自 {persona.label}] {content}"
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

        # 事件驱动的命令完成检测：为这条命令创建一个 UserCommandTracker，
        # 它在 root 节点发起的 org_delegate_task 上 register_chain、
        # 在 _mark_chain_closed 上 unregister_chain。首次激活完成 + 所有 chain
        # 关闭 + root IDLE + root inbox 空时 tracker.completed 被 set。
        # 与 tracker 并行跑一个看门狗协程，只负责"真正卡死"的预警/兜底，
        # 不参与完成判定。
        tracker_key = (org_id, target.id)
        prior = self._active_user_cmd.pop(tracker_key, None)
        if prior is not None and not prior.completed.is_set():
            # 极少见：同一 root 上两条命令重叠。先标记旧的 completed 让其
            # 看门狗/等待者退出，避免悬挂。
            prior.completed.set()

        tracker = UserCommandTracker(org_id, target.id)
        self._active_user_cmd[tracker_key] = tracker

        watchdog_task = asyncio.create_task(self._command_watchdog(tracker))

        try:
            result = await self._activate_and_run(
                org, target, tagged_content,
                chain_id=chain_id, activation_origin="user_command",
            )
            # root 首轮 ReAct 结束后立刻检查一次完成条件（无派工任务时直接命中）
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

        # 取 _latest_root_result 作为真正的"用户可见最终结果"——它只会被
        # user_command / task_delivered / delivery_followup 三类来源写入，
        # 故 CEO 回复 CFO 预算问题等中间态不会污染这里。
        final_result = self._latest_root_result.pop(org_id, None)
        if final_result is None:
            # 兜底：tracker 完成但 _latest_root_result 因过滤或异常未被写入，
            # 回退到首轮 _activate_and_run 的返回值。
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
                "组织长时间无进度，已自动暂停，此为已有阶段性结果。",
            )

        if chain_id:
            final_result["chain_id"] = chain_id
        return final_result

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

        # (c2) 如果取消的恰好是某个进行中命令的 root 节点，标记其 tracker 完成，
        # 让 send_command 的等待者立即返回——避免"用户通过 UI 取消任务后
        # HTTP 请求仍在后台挂着等 completed 事件"的死锁。
        tracker = self._active_user_cmd.get((org_id, node_id))
        if tracker is not None and not tracker.completed.is_set():
            tracker.completed.set()

        # (c3) 唤醒该节点的 org_wait_for_deliverable / inbox 等待者，避免
        # 任务被取消后 wait 还在原超时上挂着不返回。
        try:
            inbox_key = f"{org_id}:{node_id}"
            ev = self._node_inbox_events.get(inbox_key)
            if ev is not None:
                ev.set()
        except Exception:
            pass

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
        # 关链事件 → 通知所有该 org 下的 UserCommandTracker 移除此 chain 并
        # 尝试 finalize。若所有 chain 都关闭 + root IDLE + inbox 空则命令完成。
        try:
            self._tracker_unregister_chain(org_id, chain_id)
        except Exception:
            logger.debug(
                "[OrgRuntime] tracker unregister_chain failed",
                exc_info=True,
            )
        # 触发该 chain 的 wait event（如果有），让 org_wait_for_deliverable
        # 立即返回。event 一旦 set 便永久保留 set 状态，后到的 wait 会立即返回；
        # 容量超限时按 LRU 弹出最早的（弹出前已 set，无需阻塞 wait）。
        try:
            ev = self._chain_events.get(chain_id)
            if ev is not None:
                ev.set()
                # touch LRU
                self._chain_events.move_to_end(chain_id)
            while len(self._chain_events) > self._max_chain_events:
                self._chain_events.popitem(last=False)
        except Exception:
            logger.debug(
                "[OrgRuntime] chain_event set failed", exc_info=True,
            )

    def _cleanup_accepted_chain(
        self,
        org_id: str,
        chain_id: str,
        *,
        reason: str = "accepted",
        cascade_cancel_children: bool = True,
    ) -> None:
        """统一清理一条已关闭任务链的运行时状态。

        触发时机：task accept / reject / cancel。做的事：
          1. 将 chain 加入 ``_closed_chains`` 黑名单（供 `_on_node_message` 与
             `org_*` 工具做软拦截）。
          2. 清空 ``_node_current_chain`` 中所有指向该 chain 的节点绑定。
          3. 释放 messenger 的 task_affinity / delegation_depth（若未释放）。
          4. 级联把该 chain 在 ProjectStore 中的未完成子任务置为 CANCELLED，
             并广播 ``org:task_cancelled`` 事件供 UI 同步。

        纯本地状态清理，**不会**去动 mailbox 队列里已存在的消息——那部分
        由 `_on_node_message` 的软门禁负责放行/抑制。
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
        """把 ProjectStore 中以该 chain 为根的未完成子任务标记为 CANCELLED。

        只影响状态为 TODO / IN_PROGRESS / DELIVERED 的子任务；ACCEPTED 的保持不动。
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
            return {"error": f"{node.role_title} 已被冻结，无法执行任务"}
        if node.status == NodeStatus.OFFLINE:
            return {"error": f"{node.role_title} 已下线"}

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
            # 同时把 org_id/node_id 注入到 agent context，供 reasoning_engine
            # 在 verify 时查询本节点 chain-scoped/mailbox 信号（B4）。
            agent._org_context["current_org_id"] = org.id
            agent._org_context["current_node_id"] = node.id

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

            # 区分 task 真实退出原因：normal/ask_user -> 完成；
            # loop_terminated -> 被 Supervisor 强制终止；
            # max_iterations -> 超过最大迭代；
            # verify_incomplete -> TaskVerify 判定未完成但重试耗尽
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

            # 非正常退出时调用 failure_diagnoser 生成"为什么失败 + 建议"人话 payload；
            # 正常退出路径跳过以避免在热路径上做无用功。
            diagnosis: dict | None = None
            if not is_normal:
                try:
                    react_trace = getattr(re_engine, "_last_react_trace", None) if re_engine else None
                    diagnosis = _diagnose_failure(react_trace, exit_reason)
                except Exception as diag_err:
                    logger.debug(f"[OrgRuntime] failure diagnosis failed on {node.id}: {diag_err}")
                    diagnosis = None
                # 把人话摘要追加到 result_text 末尾，这样即使前端只读 chat bubble 也能看到结论
                if diagnosis:
                    try:
                        human_summary = format_human_summary(diagnosis)
                        if human_summary and human_summary not in (result_text or ""):
                            separator = "\n\n" if result_text else ""
                            result_text = (result_text or "") + separator + human_summary
                    except Exception as fmt_err:
                        logger.debug(f"[OrgRuntime] format_human_summary failed: {fmt_err}")

            # 选择与 exit_reason 匹配的事件名，供前端区分 UI 样式
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
            # 失败/终止类卡片做窗口去重，避免 verify_incomplete 重试或多路径
            # 触发同一节点同一根因被反复 emit 多张相同诊断卡片到聊天气泡。
            # 正常完成（org:task_complete）保持原行为，不做任何抑制。
            should_emit_ws = True
            if not is_normal:
                rc = (diagnosis or {}).get("root_cause", "unknown")
                if self._should_skip_diagnosis_emit(org.id, node.id, rc):
                    should_emit_ws = False
                    logger.warning(
                        f"[OrgRuntime] diagnosis emit dedupe drop: "
                        f"org={org.id} node={node.id} root_cause={rc} event={ws_event}"
                    )
            if should_emit_ws:
                await self._broadcast_ws(ws_event, ws_payload)
            if not is_normal:
                root_cause_tag = (diagnosis or {}).get("root_cause", "unknown")
                logger.warning(
                    f"[OrgRuntime] Node {node.id} ended with exit_reason={exit_reason}, "
                    f"root_cause={root_cause_tag}, emitting {event_name} (NOT task_completed)"
                )

            is_root = (node.level == 0 or not org.get_parent(node.id))
            if is_root:
                # 只有来源属于"用户可见终态"的激活才允许写入 _latest_root_result。
                # 见 _origin_from_msg_type 与 _FINAL_RESULT_ORIGINS：
                # - user_command：send_command 下发的首次激活
                # - task_delivered：下级提交交付物后唤醒 root 做综合汇报
                # - delivery_followup：验收后的后续处理
                # QUESTION/ANSWER/FEEDBACK/NOTIFICATION 等 inter-agent 通信不写入，
                # 避免 CEO 回答下级问题的文字被当成"最终结果"返回给用户。
                # 优先用显式传入的 activation_origin；若未传入（兼容旧路径）
                # 则回退读 _root_activation_origin。默认保守按 "user_command"
                # 处理，保证历史调用点（无命令跟踪）的行为不变。
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

            # 非正常结束时不触发 post-task hook（避免把"部分/失败结果"再次下发下游）
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
                "1. **只使用上述 org_* 工具**。不要调用 write_file、read_file、run_shell 等非组织工具，它们不可用；也不要用 `get_tool_info` 去探查这些被禁用的工具，对你来说一定查不到。\n"
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

        if self._suppress_post_hook.get(org_id):
            return

        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            return
        node = org.get_node(node_id)
        if not node or node.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
            return

        # 触发节点 inbox event：让 org_wait_for_deliverable 阻塞中的 coordinator
        # 立即跳出去处理新消息，避免下属在等 coordinator 决策时死等。
        # 仅对 question/escalate/feedback 这类需要立即响应的消息触发；
        # task_delivered 由 chain_event 通道触发，避免重复信号。
        try:
            if msg.msg_type in (
                MsgType.QUESTION, MsgType.ESCALATE, MsgType.FEEDBACK,
            ):
                inbox_key = f"{org_id}:{node_id}"
                ev = self._node_inbox_events.get(inbox_key)
                if ev is not None:
                    ev.set()
        except Exception:
            logger.debug("[OrgRuntime] inbox_event set failed", exc_info=True)

        # ---------- 已关闭任务链软屏障 ----------
        # 目的：任务被验收/打回/取消后，该 chain 的后续"非派工类"消息不应再
        # 唤醒 agent 的 ReAct 循环（这是用户反馈的"任务结束后仍自主派活"的
        # 根本入口之一）。放行规则：
        #   - 新的 TASK_ASSIGN（即使声明旧 chain_id，也视为一次新派工）
        #   - TASK_REJECTED（允许被打回重做）
        # 其余类型（REPORT/ANSWER/QUESTION/TASK_DELIVERED/TASK_ACCEPTED/
        # FEEDBACK/NOTIFICATION…）只广播 WebSocket 让 UI 可见，不重启 ReAct。
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
        # ---------- 软屏障结束 ----------

        active_count = self._node_active_count(org_id, node_id)

        messenger = self.get_messenger(org_id)
        pending = messenger.get_pending_count(node_id) if messenger else 0

        def _mark_dispatched() -> None:
            if messenger:
                mb = messenger.get_mailbox(node_id)
                if mb and not mb.is_paused:
                    mb.mark_handler_processed(msg.id)

        # 任一消息被派发都算一次"组织在呼吸"的进度信号。放在消息被真正投递
        # 到 agent 前，覆盖 "收到消息但当前节点并发已满、消息滞留 mailbox" 的路径。
        self._touch_trackers_for_org(org_id)

        # 按消息类型为"本次激活"计算来源标签。显式参数传入 _activate_and_run，
        # 避免并发场景下共享字典被后到消息覆盖的竞态。
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

        # 若上游通过 send_message + propagate_chain=true 接力了一条任务链，
        # 显式提示接收方在交付时复用该 task_chain_id，避免接收方自己造一个
        # 新链导致整棵任务树断裂（这是修复 delegate 误判 → send_message 兜底
        # → 链路丢失这条工程上常见的失败模式的最后一环）。
        if (
            msg.metadata.get("propagate_chain")
            and chain_id
            and msg.msg_type != MsgType.TASK_ASSIGN
        ):
            relay_from = msg.metadata.get("relay_from_node") or msg.from_node
            extra += (
                f"\n[任务链接力] 上级 {relay_from} 把 task_chain_id={chain_id} 接力给你，"
                f"完成后请用 org_submit_deliverable(task_chain_id=\"{chain_id}\") 提交，"
                "不要自己生成新的 task_chain_id。"
            )

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

    # ------------------------------------------------------------------
    # Verify-context accessors (B4 - 给 reasoning_engine 提供组织视角信号)
    # ------------------------------------------------------------------

    def get_accepted_child_count(self, org_id: str, chain_id: str) -> int:
        """严格信号：当前激活 chain 子树下已 ACCEPTED 的子任务数。

        通过 ProjectStore.find_task_by_chain 找到当前 task，再 get_subtasks
        数 status==ACCEPTED 的儿子。chain_id 缺失或 task 不存在时返回 0，
        不抛异常。仅用于「该协调者节点是否已通过下属交付完成本任务」判定。
        """
        if not chain_id:
            return 0
        try:
            from openakita.orgs.models import TaskStatus
            from openakita.orgs.project_store import ProjectStore

            store = ProjectStore(self._manager._org_dir(org_id))
            task = store.find_task_by_chain(chain_id)
            if not task:
                return 0
            children = store.get_subtasks(task.id)
            return sum(
                1 for c in children if c.status == TaskStatus.ACCEPTED
            )
        except Exception as exc:  # pragma: no cover — 防御性
            logger.debug(
                "[Verify] get_accepted_child_count(%s, %s) failed: %s",
                org_id, chain_id, exc,
            )
            return 0

    def has_recent_accepted_signal(
        self,
        org_id: str,
        node_id: str,
        window_secs: float = 60.0,
    ) -> bool:
        """弱信号兜底：该节点最近 N 秒是否作为「验收方」处理过 task_accepted。

        用于严格信号拿不到时（chain_id 缺失 / 没有 ProjectStore task）的兜底，
        只在很短时间窗口内成立，避免把过去任务的成果错误带入新任务。
        """
        if not node_id:
            return False
        try:
            from datetime import datetime, timedelta, timezone

            store = self.get_event_store(org_id)
            if store is None:
                return False
            cutoff = datetime.now(timezone.utc) - timedelta(
                seconds=max(1.0, float(window_secs)),
            )
            cutoff_iso = cutoff.isoformat()
            recent = store.query(
                event_type="task_accepted",
                actor=node_id,
                since=cutoff_iso,
                limit=5,
            )
            return bool(recent)
        except Exception as exc:  # pragma: no cover — 防御性
            logger.debug(
                "[Verify] has_recent_accepted_signal(%s, %s) failed: %s",
                org_id, node_id, exc,
            )
            return False

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
        # 任一节点状态切换都算一次"组织在呼吸"的进度信号，重置命令看门狗
        # 计时器；同时在节点进入 IDLE 时有可能满足命令完成条件，触发一次
        # 终态检测（极廉价，未命中 _active_user_cmd 时 O(0)）。
        self._touch_trackers_for_org(org.id)
        if new_status == NodeStatus.IDLE:
            self._maybe_finalize_trackers_for_org(org.id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _activate_org(self, org: Organization) -> None:
        """Set up runtime infrastructure for an organization."""
        # 重新激活同一 org 时，清理"最近被停止"标记，避免新 session 的
        # 工具调用被误判成"组织已停止"
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

        # 组织被注销时释放所有挂起的命令 tracker 与 origin 标签，避免
        # send_command 的 await tracker.completed.wait() 永久挂起。
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

            # 同 `_on_node_message` 的软屏障：已关闭 chain 的非派工消息不再激活 ReAct，
            # 只标记为已处理让其从队列中"自然消失"。否则 drain 路径会绕过 handler 门禁。
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

            # 默认关闭"任务完成自动通知父级"——这是用户反馈的"组织莫名其妙自主运行"
            # 的核心源头之一：子节点完成后 runtime 主动唤醒父节点做 ReAct，父节点
            # 的 LLM 经常忽略"禁止派新活"的 prompt 指令，继续派更多任务。
            #
            # 保留 drain 路径（上面 `_drain_node_pending(parent)`）——若父节点真有
            # 待处理的 deliverable/question，依然会被正常推进。
            # 如需要保持历史行为，可把 org_post_task_notify_parent 设为 True。
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
                f"[通知] {role_title} 已完成一项任务。\n"
                f"如有待验收的交付物，请处理。如无，则无需任何操作。\n"
                f"⚠️ 禁止：不要分配新任务、不要扩展工作范围、不要主动发起任何工作。"
            )
            # post_task_notify 不是"用户命令路径"，不应写入 _latest_root_result，
            # 否则父节点处理这条通知产生的文本会污染用户侧最终结果。
            await self._activate_and_run(
                org, parent, prompt, activation_origin="post_task_notify",
            )
        except Exception as e:
            logger.debug(f"[OrgRuntime] Post-task hook error: {e}")

    _STOP_KEYWORDS = frozenset({
        "暂停", "停止", "取消", "别做了", "先不做", "到此为止",
        "不要继续", "停下来", "先暂停", "不用做了", "够了",
        "终止", "中止", "全部停止", "不做了",
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
        # Wake up any org_wait_for_deliverable / inbox waiters in this org
        # so they unblock with "cancelled" semantics rather than waiting full
        # timeout. Iterate over all chain/inbox events touching this org.
        try:
            org_prefix = f"{org_id}:"
            for k in list(self._node_inbox_events.keys()):
                if k.startswith(org_prefix):
                    ev = self._node_inbox_events.get(k)
                    if ev is not None:
                        ev.set()
            # chain events: we don't know which chain belongs to which org
            # without walking ProjectStore, so set every event whose chain is
            # already in this org's closed_chains bucket. Other waits will
            # naturally timeout.
            bucket = self._closed_chains.get(org_id) or {}
            for cid in bucket:
                ev = self._chain_events.get(cid)
                if ev is not None:
                    ev.set()
        except Exception:
            logger.debug(
                "[OrgRuntime] soft_stop wake waiters failed", exc_info=True,
            )
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

    def _collect_chain_subtree(
        self, root_chain_id: str | None,
    ) -> set[str]:
        """Walk forward from ``root_chain_id`` collecting all descendant chains.

        Only used by ``_maybe_finalize_tracker`` when
        ``org_chain_parent_enforced`` is enabled. Returns the set of chain ids
        in the subtree (including the root). Empty when ``root_chain_id`` is
        ``None``.
        """
        if not root_chain_id:
            return set()
        subtree: set[str] = {root_chain_id}
        # _chain_parent maps child→parent; reverse-walk by scanning entries.
        # Sub-tree size is small in practice (<= 数十), so O(n*depth) is fine.
        changed = True
        while changed:
            changed = False
            for child, parent in self._chain_parent.items():
                if parent in subtree and child not in subtree:
                    subtree.add(child)
                    changed = True
        return subtree

    def _is_subtree_fully_closed(
        self, tracker: UserCommandTracker,
    ) -> bool:
        """Return True iff every chain in the tracker's chain-subtree is closed.

        Falls back to the legacy ``open_chains`` check when
        ``org_chain_parent_enforced`` is disabled or the tracker has no
        ``root_chain_id`` yet (e.g. root never delegated).
        """
        try:
            from openakita.config import settings as _settings
            enforced = bool(getattr(
                _settings, "org_chain_parent_enforced", True,
            ))
        except Exception:
            enforced = True

        if not enforced or not tracker.root_chain_id:
            return not tracker.open_chains

        subtree = self._collect_chain_subtree(tracker.root_chain_id)
        bucket = self._closed_chains.get(tracker.org_id) or {}
        # A chain in the subtree is "open" if it's not yet in closed_chains.
        return all(cid in bucket for cid in subtree)

    def _maybe_finalize_tracker(
        self, tracker: UserCommandTracker,
    ) -> None:
        """If completion conditions are met, advance tracker state.

        Two-phase completion when ``org_root_post_summary`` is enabled:
          1. running → awaiting_summary: subtree closed + root IDLE + no
             active delegations. Push a ``task_complete`` notification to
             the root inbox so the root can produce a final summary ReAct.
          2. awaiting_summary → done: same conditions hold for a *second*
             time (root finished its summary ReAct and is back to IDLE).
        When the flag is disabled, completion is set immediately on first
        match (legacy behaviour).
        """
        if tracker.completed.is_set():
            return
        if not self._is_subtree_fully_closed(tracker):
            self._log_finalize_decision(tracker, "subtree_not_closed")
            return
        org = self.get_org(tracker.org_id)
        if not org:
            tracker.completed.set()
            self._log_finalize_decision(tracker, "no_org")
            return
        root = org.get_node(tracker.root_node_id)
        if not root or root.status != NodeStatus.IDLE:
            self._log_finalize_decision(
                tracker, "root_not_idle",
                root_status=root.status.value if root else None,
            )
            return
        if self._has_active_delegations(tracker.org_id, tracker.root_node_id):
            self._log_finalize_decision(tracker, "active_delegations")
            return

        try:
            from openakita.config import settings as _settings
            post_summary_enabled = bool(getattr(
                _settings, "org_root_post_summary", True,
            ))
        except Exception:
            post_summary_enabled = True

        if not post_summary_enabled:
            self._log_finalize_decision(tracker, "completed_legacy")
            tracker.completed.set()
            return

        # Two-phase state machine.
        if tracker.state == "running":
            if self._push_root_summary_prompt(tracker):
                tracker.state = "awaiting_summary"
                tracker.summary_pushed_at = time.monotonic()
                self._log_finalize_decision(tracker, "summary_pushed")
            else:
                # push failed (no children, no chains, or already pushed) →
                # treat as legacy direct completion to avoid hanging.
                tracker.completed.set()
                self._log_finalize_decision(tracker, "completed_no_summary")
            return

        if tracker.state == "awaiting_summary":
            tracker.state = "done"
            tracker.completed.set()
            self._log_finalize_decision(tracker, "completed_after_summary")
            return

        # Unknown state — defensive, complete to avoid hanging.
        tracker.completed.set()
        self._log_finalize_decision(tracker, "completed_unknown_state")

    def _log_finalize_decision(
        self,
        tracker: UserCommandTracker,
        decision: str,
        **extra: Any,
    ) -> None:
        """Structured debug log for tracker finalize decisions (L. observability).

        Helps diagnose "why didn't / did this command finish" without trawling
        events.jsonl. DEBUG level: opt-in by lowering openakita.orgs logger.
        """
        try:
            subtree = self._collect_chain_subtree(tracker.root_chain_id)
            payload = {
                "org": tracker.org_id,
                "root": tracker.root_node_id,
                "cmd": tracker.command_id or "",
                "decision": decision,
                "state": tracker.state,
                "open_chains": len(tracker.open_chains),
                "subtree_size": len(subtree),
                "subtree_closed": sum(
                    1 for c in subtree
                    if c in (self._closed_chains.get(tracker.org_id) or {})
                ),
            }
            payload.update(extra)
            logger.debug("[Finalize] %s", payload)
        except Exception:
            logger.debug("[Finalize] log emit failed", exc_info=True)

    def _push_root_summary_prompt(
        self, tracker: UserCommandTracker,
    ) -> bool:
        """Wake up the root node so it can produce a final summary ReAct.

        Two side effects:
          1. Push a ``task_complete`` inbox card (UI signal).
          2. Schedule an ``_activate_and_run`` task that runs the root with
             a "summarise everything" prompt. When that ReAct finishes the
             root goes IDLE → ``_maybe_finalize_trackers_for_org`` runs
             again → tracker advances ``awaiting_summary`` → ``done``.

        Returns True on successful schedule. Debounced by
        ``tracker.summary_pushed_at`` so repeated finalize attempts don't
        re-wake the root.
        """
        if tracker.summary_pushed_at > 0:
            return False
        org = self.get_org(tracker.org_id)
        if not org:
            return False
        root = org.get_node(tracker.root_node_id)
        if not root:
            return False

        # Build a brief recap from the closed subtree (best-effort).
        subtree = self._collect_chain_subtree(tracker.root_chain_id)
        recap_parts: list[str] = []
        try:
            from openakita.orgs.project_store import ProjectStore as _PS
            store = _PS(self._manager._org_dir(tracker.org_id))
            for cid in list(subtree)[:10]:
                task = store.find_task_by_chain(cid)
                if task:
                    title = (task.title or "")[:60]
                    assignee = task.assignee_node_id or ""
                    recap_parts.append(
                        f"- {assignee}: {title} [{task.status.value}]"
                    )
        except Exception:
            logger.debug(
                "[PushSummary] project_store recap failed", exc_info=True,
            )

        recap = "\n".join(recap_parts) if recap_parts else (
            "（无可识别的子任务记录，请直接根据已收到的下级 deliverable 汇总）"
        )
        body = (
            "[用户指令最终汇总] 你最初接到的用户指令所触发的所有委派任务均已关闭。"
            "请基于下级各自交付的成果，向用户输出一份完整的最终汇总——"
            "覆盖每位下级的产出要点、关键文件/链接、已完成程度、"
            "以及任何遗留风险或下一步建议。\n\n"
            "已关闭的子任务概览：\n" + recap + "\n\n"
            "重要约束：本次激活只用于产出汇总文本，"
            "禁止再调 org_delegate_task / org_submit_deliverable / "
            "org_wait_for_deliverable 等会重启任务流转的工具，"
            "直接以自然语言回复用户即可。"
        )

        # Inbox card (UI/notification only).
        try:
            inbox = self._inbox
            if inbox is not None:
                inbox.push_task_complete(
                    tracker.org_id,
                    tracker.root_node_id,
                    task_name=(tracker.command_id or "用户指令"),
                    result_summary=body[:500],
                )
        except Exception:
            logger.debug(
                "[PushSummary] inbox push failed", exc_info=True,
            )

        # Actually wake the root for a summary ReAct.
        try:
            asyncio.create_task(
                self._activate_and_run(
                    org, root, body,
                    activation_origin="delivery_followup",
                ),
                name=f"summary_followup:{tracker.org_id}:{tracker.root_node_id}",
            )
        except RuntimeError:
            # No running loop — extremely unlikely in production but fall back
            # to direct completion to avoid hanging tests.
            logger.debug(
                "[PushSummary] no running loop; mark completed directly",
            )
            return False
        except Exception:
            logger.debug(
                "[PushSummary] schedule activate failed", exc_info=True,
            )
            return False
        return True

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
                        if self._suppress_post_hook.get(org_id):
                            continue
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

    def _mark_effective_action(self, org_id: str, node_id: str) -> None:
        """记录节点产生了一次"有效 outbound 行动"。

        由 OrgToolHandler 在 ``org_delegate_task`` / ``org_send_message`` /
        ``org_reply_message`` / ``org_submit_deliverable`` / ``org_escalate``
        成功完成后调用。

        作用：
        1. 重置 ``_idle_node_ineffective`` 计数（节点又"活"过来了）
        2. 重置 ``_idle_node_thresholds`` 到 base（下一次 idle 重新从 120s 起）
        3. 清掉 ``_idle_probe_pending_since``（不再把这一轮算成无效唤醒）
        4. 同步 ``_node_last_effective`` 时间戳
        5. 节点级"有效"动作意味着组织也活跃，清掉 ``_idle_org_quiet_since``
        """
        cache_key = f"{org_id}:{node_id}"
        now = time.monotonic()
        self._node_last_effective[cache_key] = now
        self._idle_node_ineffective.pop(cache_key, None)
        self._idle_node_thresholds.pop(cache_key, None)
        self._idle_probe_pending_since.pop(cache_key, None)
        self._idle_org_quiet_since.pop(org_id, None)

    def _on_inbound_for_node(self, org_id: str, node_id: str) -> None:
        """节点收到 inbound（task/message/reply/feedback）时调用。

        由 OrgMessenger 在投递成功后回调。语义：节点又有真正的外部输入了，
        重置无效唤醒计数和 threshold；这是从"已永久暂停"恢复的唯一入口。
        """
        cache_key = f"{org_id}:{node_id}"
        now = time.monotonic()
        self._node_last_inbound[cache_key] = now
        self._idle_node_ineffective.pop(cache_key, None)
        self._idle_node_thresholds.pop(cache_key, None)
        self._idle_probe_pending_since.pop(cache_key, None)
        self._idle_org_quiet_since.pop(org_id, None)

    def _has_org_external_work(self, org: Organization) -> bool:
        """判断组织是否还有"外部"待处理工作（用于组织级熔断决策）。

        条件（任一为真即视为"还有活"）：
        - 任一节点 status != IDLE / ERROR / FROZEN / OFFLINE
        - 任一节点 mailbox pending > 0
        - 存在未关闭的任务 chain（从 _chain_delegation_depth 推断）
        - 存在 active user command tracker
        """
        for node in org.nodes:
            if node.status not in (
                NodeStatus.IDLE, NodeStatus.ERROR,
                NodeStatus.FROZEN, NodeStatus.OFFLINE,
            ):
                return True
        messenger = self.get_messenger(org.id)
        if messenger:
            for node in org.nodes:
                if messenger.get_pending_count(node.id) > 0:
                    return True
        for chain_id in self._chain_delegation_depth:
            if not self.is_chain_closed(org.id, chain_id):
                return True
        for (oid, _root), tracker in self._active_user_cmd.items():
            if oid == org.id and not tracker.completed.is_set():
                return True
        return False

    async def _idle_probe_loop(self, org_id: str) -> None:
        """Periodically check for idle nodes and prompt them to seek work.

        实例级 threshold：跨 IDLE/ACTIVE 切换持久存在，不会被 status 变化清零。
        节点级无效唤醒计数：连续被 probe 后既未产生有效 outbound 行动
        也未收到新 inbound 时累加；达到 ``_idle_max_ineffective`` 后该节点的
        idle probe 被永久暂停，直到 ``_on_inbound_for_node`` 被调用。
        组织级熔断：当整个组织已"安静" ``_idle_org_quiet_grace`` 秒（全员 IDLE
        且无 pending message / 未闭合 chain / 活跃 user command）时，
        loop 进入 ``_idle_org_silent_interval`` 间隔的低频心跳模式。
        """
        while True:
            try:
                await asyncio.sleep(30)
                org = self.get_org(org_id)
                if not org or org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                    break

                now = time.monotonic()

                # ── 组织级熔断：判断是否进入"全员安静"状态 ──
                has_work = self._has_org_external_work(org)
                if has_work:
                    self._idle_org_quiet_since.pop(org_id, None)
                else:
                    quiet_since = self._idle_org_quiet_since.get(org_id)
                    if quiet_since is None:
                        self._idle_org_quiet_since[org_id] = now
                    elif (now - quiet_since) >= self._idle_org_quiet_grace:
                        # 已熔断：仅 root 节点以低频心跳被探测，其它节点全部跳过。
                        # 心跳间隔由 _idle_org_silent_interval 控制。
                        roots = org.get_root_nodes()
                        for root in roots:
                            cache_key = f"{org_id}:{root.id}"
                            last_probe = self._idle_node_last_probed.get(cache_key, 0)
                            if (now - last_probe) < self._idle_org_silent_interval:
                                continue
                            if root.status != NodeStatus.IDLE or root.is_clone:
                                continue
                            messenger = self.get_messenger(org_id)
                            if messenger and messenger.get_pending_count(root.id) > 0:
                                continue
                            if self._suppress_post_hook.get(org_id):
                                continue
                            self._idle_node_last_probed[cache_key] = now
                            self._idle_probe_pending_since[cache_key] = now
                            prompt = (
                                "[空闲心跳] 组织已长时间无外部任务输入。\n"
                                "请简要确认当前状态。如无新工作，仅回复一句'保持待命'即可，"
                                "无需调用任何 org_* 工具。"
                            )
                            await self._activate_and_run(
                                org, root, prompt, activation_origin="idle_probe",
                            )
                            break
                        continue

                for node in org.nodes:
                    if node.status != NodeStatus.IDLE:
                        # 注意：刻意不再 pop _idle_node_thresholds —— 上一次
                        # 自适应增长的状态保留到下次 IDLE，避免 IDLE↔ACTIVE
                        # 抖动把 threshold 重置回 base 的旧 bug。
                        continue
                    if node.is_clone:
                        continue

                    cache_key = f"{org_id}:{node.id}"

                    # 检查：该节点是否已被永久暂停（无效唤醒达到上限）
                    ineffective = self._idle_node_ineffective.get(cache_key, 0)
                    if ineffective >= self._idle_max_ineffective:
                        continue

                    # 上一轮 probe 是否产生了有效行动？（先于本轮 probe 判定）
                    pending_since = self._idle_probe_pending_since.get(cache_key)
                    if pending_since is not None and (now - pending_since) >= 30:
                        last_eff = self._node_last_effective.get(cache_key, 0)
                        last_inb = self._node_last_inbound.get(cache_key, 0)
                        if last_eff <= pending_since and last_inb <= pending_since:
                            self._idle_node_ineffective[cache_key] = ineffective + 1
                            ineffective += 1
                            if ineffective >= self._idle_max_ineffective:
                                logger.info(
                                    "[OrgRuntime] idle probe paused for %s/%s "
                                    "after %d ineffective wakeups",
                                    org_id, node.id, ineffective,
                                )
                        self._idle_probe_pending_since.pop(cache_key, None)
                        if ineffective >= self._idle_max_ineffective:
                            continue

                    last_active = self._node_last_activity.get(cache_key, 0)
                    if last_active <= 0:
                        cached = self._agent_cache.get(cache_key)
                        last_active = cached.last_used if cached else 0
                    idle_secs = now - last_active if last_active > 0 else 0

                    threshold = self._idle_node_thresholds.get(
                        cache_key, self._idle_base_threshold,
                    )

                    if 0 < idle_secs >= threshold:
                        last_probe = self._idle_node_last_probed.get(cache_key, 0)
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

                        self._idle_node_last_probed[cache_key] = now
                        self._idle_node_thresholds[cache_key] = min(
                            threshold * 1.5, self._idle_max_threshold,
                        )
                        self._idle_probe_pending_since[cache_key] = now
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
        ext_label = self._FILE_EXT_LABELS.get(ext, "文件")

        attachment = {
            "filename": resolved_name,
            "path": str(p),
            "size_bytes": size_bytes,
        }

        bb = self.get_blackboard(org_id)
        if not bb:
            return None

        entry = bb.write_org(
            content=f"📎 产出{ext_label}：**{resolved_name}**\n📂 路径：`{str(p)}`",
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

        content_for_task = f"📎 产出文件：**{resolved_name}**\n📂 路径：`{str(p)}`"
        if text_preview:
            content_for_task += (
                f"\n\n<details><summary>文件内容预览</summary>\n\n"
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
                # Some code paths append "\n\n[执行日志]..." after the JSON.
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
