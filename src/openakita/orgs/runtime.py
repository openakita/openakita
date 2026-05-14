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

from ..core.response_handler import request_expects_artifact
from .blackboard import OrgBlackboard
from .event_store import OrgEventStore
from .failure_diagnoser import format_human_summary
from .failure_diagnoser import is_soft_verify_incomplete as _is_soft_verify_incomplete
from .failure_diagnoser import summarize as _diagnose_failure
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
from .tools import build_org_node_tools

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
        # 区分 auto_stopped 的来源：True 表示由用户主动调用
        # `cancel_user_command` 强制终止；False 表示由 _command_watchdog
        # 卡死兜底触发。仅影响 send_command 终态文案，不改变流程。
        "user_cancelled",
        "state",
        "summary_pushed_at",
        # BUG-3：保存当前命令的用户原始指令内容，供子节点 system prompt
        # 渲染（identity.build_org_context_prompt）和 _handle_org_delegate_task
        # 注入"父任务硬边界"时取用。命令结束时 tracker 一并被 pop，自动失效。
        "user_command_content",
        # BUG-5：finalize phase 事件去重，避免同 phase 重复 emit。
        "_last_phase_emitted",
    )

    def __init__(
        self,
        org_id: str,
        root_node_id: str,
        command_id: str | None = None,
        user_command_content: str = "",
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
        self.user_cancelled: bool = False
        # See class docstring for state machine details.
        self.state: str = "running"
        # monotonic time when summary inbox push happened (debounce).
        self.summary_pushed_at: float = 0.0
        self.user_command_content: str = user_command_content or ""
        self._last_phase_emitted: str | None = None

    def _touch(self) -> None:
        self.last_progress_at = time.monotonic()

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
        self._closed_chains: dict[str, OrderedDict[str, float]] = {}
        self._closed_chain_max_per_org: int = 512
        # Root 节点已在「某次主任务」里通过 org_accept_deliverable 验收过的
        # task_chain_id 集合（按 org 隔离）。后续到达同 chain_id 的 TASK_DELIVERED
        # 视为「已被并入主任务汇总」，drain / mailbox 路径都让 root_delivery_bypass
        # 失效 → 走已有的「closed chain skip」分支（mark_processed + 不激活 ReAct）。
        # 关键：不影响首次到达——P0-1「root 节点最后总结」修复完整保留，仅去重
        # 主任务结束后 mailbox 残留消息触发的「补汇总」空跑 ReAct（每次约 150K
        # tokens 浪费，详见 2026-04-28 13:42:53 _134209 现象）。
        # OrderedDict + LRU 上限：与 _closed_chains 同款设计，防长跑组织内存膨胀。
        self._root_processed_chains: dict[str, OrderedDict[str, float]] = {}
        self._root_processed_chain_max_per_org: int = 512
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
        # 窗口 300s（5min）：原 30s 太短，verify_incomplete 在长任务里很容易跨过
        # 30s 再次触发同根因诊断，造成聊天里反复出现"为什么失败"。
        self._recent_diagnosis_emit: dict[tuple[str, str, str], float] = {}
        self._diagnosis_emit_window_secs: float = 300.0

        # 节点本任务内已成功登记的文件数量（_register_file_output 成功 +1）。
        # 在 _activate_and_run_inner 进入 _run_agent_task 之前清零，agent.chat
        # 返回后读取。auto-persist 兜底仅在 counter == 0 时触发，确保不会和
        # LLM 自己调 write_file/generate_image/org_submit_deliverable 已经
        # 产生的真实附件重复落盘。
        # key = "{org_id}:{node_id}"（与 _node_last_activity 对齐），值 = int。
        self._node_files_registered_in_task: dict[str, int] = {}

        # 工作台节点：本任务内由 plugin tool hook 自动登记的附件清单。
        # _record_plugin_asset_output 成功 register 时 append，
        # _handle_org_submit_deliverable 在 LLM 未声明 file_attachments 时
        # 自动取用，避免插件节点必须人工列附件。同样在每次任务起手清空。
        # key 与 _node_files_registered_in_task 对齐：``{org_id}:{node_id}``。
        self._node_plugin_attachments_in_task: dict[str, list[dict]] = {}
        # 工作台节点：本任务内插件工具返回 ok=False 且 terminal=True 时记录
        # 失败详情。如果叶子工作台随后没有提交交付物，运行时会把本轮视为失败
        # 并关闭 chain，避免组织命令永远等不到交付。
        self._node_plugin_failures_in_task: dict[str, dict] = {}

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
        command_id: str | None = None,
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

        tracker = UserCommandTracker(
            org_id, target.id,
            command_id=command_id,
            user_command_content=content,
        )
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

        if final_result.get("usable_incomplete"):
            final_result.setdefault(
                "warning",
                "最终汇总已生成，但任务校验器未确认完成；已保留完整汇总内容。",
            )

        if tracker.auto_stopped:
            final_result["stopped_by_watchdog"] = True
            final_result["status"] = "stopped_no_progress"
            if tracker.user_cancelled:
                final_result["cancelled_by_user"] = True
                # 覆盖 warning（即使被前置流程 setdefault 过也要换成"用户主动"文案）
                final_result["warning"] = (
                    "已按用户请求强制终止当前任务，可立即发送新指令。"
                )
            else:
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

    def _mark_chain_processed_by_root(self, org_id: str, chain_id: str) -> None:
        """登记 chain_id 已被 root 节点本次主任务通过 org_accept_deliverable
        处理过。后续到达同 chain_id 的 TASK_DELIVERED 视为「已被并入主任务汇总」，
        在 `_on_node_message` / `_drain_node_pending` 中走「closed chain skip」分支，
        避免 mailbox 残留消息触发空跑「补汇总」ReAct（每次约 150K tokens 浪费）。

        与 `_mark_chain_closed` 的区别：本集合只代表「root 节点已综合处理过」，
        不影响 `is_chain_closed` 的「下属任务链是否被验收」语义。
        """
        if not org_id or not chain_id:
            return
        bucket = self._root_processed_chains.get(org_id)
        if bucket is None:
            bucket = OrderedDict()
            self._root_processed_chains[org_id] = bucket
        if chain_id in bucket:
            bucket.move_to_end(chain_id)
        else:
            bucket[chain_id] = time.time()
            while len(bucket) > self._root_processed_chain_max_per_org:
                bucket.popitem(last=False)

    def _is_chain_processed_by_root(
        self, org_id: str, chain_id: str | None,
    ) -> bool:
        """查询 chain_id 是否已被 root 节点本次主任务处理过。"""
        if not org_id or not chain_id:
            return False
        bucket = self._root_processed_chains.get(org_id)
        return bool(bucket and chain_id in bucket)

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

            # per-task 文件计数器清零：本任务内每次成功的 _register_file_output
            # 都会让该 counter +1。auto-persist 兜底仅在 counter==0 时触发，
            # 杜绝"LLM 已自己写过文件 + 系统又兜底落盘"的双写。
            self._node_files_registered_in_task[cache_key] = 0
            # plugin hook 登记附件缓冲清零：本任务内由 _record_plugin_asset_output
            # 产生的附件会累计到这里，供 _handle_org_submit_deliverable 在 LLM
            # 未传 file_attachments 时自动取用。
            self._node_plugin_attachments_in_task[cache_key] = []
            self._node_plugin_failures_in_task.pop(cache_key, None)

            result_text = await self._run_agent_task(
                agent, prompt, session_id, org, node,
            )

            if org.id not in self._active_orgs:
                return {"node_id": node.id, "result": result_text}

            # 区分 task 真实退出原因：normal/ask_user/waiting_user -> 完成或中性收口；
            # loop_terminated -> 被 Supervisor 强制终止；
            # max_iterations -> 超过最大迭代；
            # verify_incomplete -> TaskVerify 判定未完成但重试耗尽
            #
            # 软 verify_incomplete（is_soft_verify=True）：exit_reason=verify_incomplete
            # 但 trace 中已存在成功的 org_accept_deliverable，说明协调者节点
            # 实际已通过下属交付完成本任务；这种情况直接走 is_normal 路径，
            # 触发 _post_task_hook 唤醒父级 drain，避免上级因子节点未发出
            # "完成"信号而陷入 5+ 分钟 idle 等待（CmdWatchdog warn）。
            exit_reason = "normal"
            re_engine = None
            react_trace: list[dict] | None = None
            try:
                re_engine = getattr(agent, "reasoning_engine", None)
                if re_engine is not None:
                    exit_reason = getattr(re_engine, "_last_exit_reason", "normal") or "normal"
                    react_trace = getattr(re_engine, "_last_react_trace", None)
            except Exception:
                exit_reason = "normal"
                re_engine = None
                react_trace = None

            # ======================================================
            # 文件交付兜底（auto-persist final answer）
            # ------------------------------------------------------
            # 触发条件（必须全部满足，任一不满足直接跳过）：
            #   1. org.auto_persist_final_answer 为 True（per-org 开关，
            #      默认 True，UI 在组织设置页可关）；
            #   2. 用户原始 prompt 命中 request_expects_artifact —— 用户确实
            #      在要"附件/文件类"成果，否则强行落盘是噪音；
            #   3. 本任务内 _register_file_output 计数 == 0 —— LLM 没自己
            #      产出过任何文件（写过的不重复）；
            #   4. result_text 是有意义的长文（≥200 字符）—— 短回复落盘
            #      只会污染 workspace。
            # 任何异常仅 warning，不影响主流程。
            persisted_attachment: dict | None = None
            files_registered = self._node_files_registered_in_task.get(cache_key, 0)
            try:
                # files_registered>0 时把"方案/策划/计划/报告"等弱信号升级为强：
                # 子节点已经写过文件，coordinator 必须走 deliver_artifacts 转发。
                expects_artifact = request_expects_artifact(
                    prompt, has_produced_files=files_registered > 0
                )
            except Exception:
                expects_artifact = False
            auto_persist_enabled = bool(
                getattr(org, "auto_persist_final_answer", True)
            )
            if (
                auto_persist_enabled
                and expects_artifact
                and files_registered == 0
                and isinstance(result_text, str)
                and len(result_text.strip()) >= 200
            ):
                try:
                    workspace_for_persist = self._resolve_org_workspace(org)
                    persisted_attachment = self._tool_handler.auto_persist_node_final_answer(
                        org_id=org.id,
                        node_id=node.id,
                        chain_id=chain_id,
                        title=(prompt or "")[:60].strip() or "final_answer",
                        body=result_text,
                        workspace=workspace_for_persist,
                    )
                except Exception:
                    logger.warning(
                        "[OrgRuntime] auto_persist_node_final_answer hook failed",
                        exc_info=True,
                    )
                    persisted_attachment = None

            # 子节点合成 TASK_DELIVERED（auto-persist 兜底最后一公里）：
            #
            # P0-5 修订：旧注释说"关闭父节点 wait_for_deliverable"——这是误导。
            # 父节点的 `org_wait_for_deliverable` 监听的是 `inbox_event` /
            # `chain_event`，TASK_DELIVERED 消息进 mailbox 时**不会**额外发这两
            # 个事件，所以这里合成的消息**无法**直接结束 wait。它真正的作用是：
            #   1) 进父节点 mailbox → 后续父节点被激活时能看到"下属已交付 + 附件"
            #   2) emit `task_delivered` event + ws → 前端时间线/项目卡片
            #      自动更新为 delivered，避免一直停在 in_progress
            #   3) `_link_project_task(status="delivered")` → ProjectTask 状态闭合
            #   4) `_on_inbound_for_node(parent)` → 父节点 mailbox watcher
            #      被推一下，让其在下次空闲时 drain
            # 触发条件（P0-4 收紧）：
            #   - 本节点不是 root（root 直接对用户回复，没有"父节点"概念）
            #   - 本轮 LLM 没自己调过 org_submit_deliverable（防双发）
            #   - chain_id 存在
            #   - **activation_origin == "task_assign"**：仅当本次激活是被父节点
            #     `org_delegate_task` 派下来时才合成。其它来源（user_command /
            #     question / answer / feedback / report / handshake / broadcast …）
            #     语义上不是"交付场景"，合成 TASK_DELIVERED 反而把 ProjectTask
            #     状态错误推进到 delivered，污染验收流程。
            if persisted_attachment is not None:
                try:
                    is_root_for_delivery = (
                        node.level == 0 or not org.get_parent(node.id)
                    )
                    submit_called = self._react_trace_has_tool(
                        react_trace, "org_submit_deliverable",
                    )
                    is_task_assign_origin = (activation_origin == "task_assign")
                    if (
                        not is_root_for_delivery
                        and not submit_called
                        and chain_id
                        and is_task_assign_origin
                    ):
                        await self._synthesize_task_delivered_to_parent(
                            org=org,
                            from_node=node,
                            chain_id=chain_id,
                            deliverable_text=result_text,
                            attachment=persisted_attachment,
                        )
                    elif persisted_attachment is not None and not is_task_assign_origin:
                        logger.info(
                            "[OrgRuntime] synth-TASK_DELIVERED skipped: "
                            "activation_origin=%s is not task_assign (org=%s node=%s "
                            "chain=%s)",
                            activation_origin, org.id, node.id, chain_id,
                        )
                except Exception:
                    logger.warning(
                        "[OrgRuntime] synthesize task_delivered failed",
                        exc_info=True,
                    )

            try:
                is_soft_verify = _is_soft_verify_incomplete(exit_reason, react_trace)
            except Exception:
                is_soft_verify = False

            # P0-2：根节点禁止走 soft verify 降级。根节点是用户最终看到的发言人，
            # verify_incomplete 多半意味着"该综合下属交付给出最终回复，但 LLM 没
            # 给"。如果根节点也降级 soft，会被静默判定为"完成"，于是出现用户
            # 反馈的"最后由子节点说话、根节点没总结"问题。对根节点保持硬 verify_incomplete，
            # 让 reasoning_engine 的重试 / 失败路径有机会强制 LLM 再总结一次。
            try:
                _is_root_node = org.get_parent(node.id) is None
            except Exception:
                _is_root_node = False
            if _is_root_node and is_soft_verify:
                logger.info(
                    "[OrgRuntime] root node %s downgrade-to-soft denied; "
                    "keep verify_incomplete strict so root will not silently exit "
                    "without final summary.", node.id,
                )
                is_soft_verify = False

            is_normal = exit_reason in ("normal", "ask_user", "waiting_user") or is_soft_verify
            is_terminated = exit_reason == "loop_terminated"
            plugin_failure = self._node_plugin_failures_in_task.get(cache_key)
            submit_called_for_failure = self._react_trace_has_tool(
                react_trace, "org_submit_deliverable",
            )
            if (
                is_normal
                and plugin_failure
                and activation_origin == "task_assign"
                and chain_id
                and not submit_called_for_failure
            ):
                exit_reason = "plugin_tool_failed"
                is_normal = False
                is_terminated = False
                msg = (
                    plugin_failure.get("error")
                    or plugin_failure.get("message")
                    or "工作台工具调用失败"
                )
                if msg and msg not in (result_text or ""):
                    result_text = ((result_text or "").rstrip() + "\n\n" + str(msg)).strip()

            status_reason = "task_completed" if is_normal else (
                "task_terminated" if is_terminated else "task_failed"
            )
            self._set_node_status(org, node, NodeStatus.IDLE, status_reason)
            if is_normal:
                org.total_tasks_completed += 1
                self._node_consecutive_failures.pop(f"{org.id}:{node.id}", None)
                self._org_quota_failures.pop(org.id, None)

            # ── Root 节点 chain 去重登记 ───────────────────────────
            # 当 root 节点在「正常完成」的主任务里通过 org_accept_deliverable
            # 验收过任意下属 chain 时，把这些 chain_id 登记到 _root_processed_chains。
            # 后续 mailbox 中残留的同 chain TASK_DELIVERED 在 _on_node_message /
            # _drain_node_pending 中会被 root_delivery_bypass=False 命中，走
            # 「closed chain skip」分支，避免空跑「补汇总」ReAct（每次 ~150K tokens）。
            #
            # 严格三条件（缺一不登记，避免回归 P0-1「最后由子节点说话」bug）：
            #   1. _is_root_node = True（仅 root 关心去重，子节点 accept 不应登记）
            #   2. is_normal = True（失败/终止任务不污染集合）
            #   3. react_trace 里有成功的 org_accept_deliverable
            try:
                if _is_root_node and is_normal:
                    accepted_chains = self._extract_accepted_chain_ids(react_trace)
                    for cid in accepted_chains:
                        self._mark_chain_processed_by_root(org.id, cid)
                    if accepted_chains:
                        logger.info(
                            "[OrgRuntime] root node %s task_completed: "
                            "registered %d processed chain(s) for dedup: %s",
                            node.id, len(accepted_chains),
                            [c[:8] for c in accepted_chains],
                        )
            except Exception:
                logger.debug(
                    "[OrgRuntime] register root processed chains failed",
                    exc_info=True,
                )

            await self._save_org(org)
            self._heartbeat.record_activity(org.id)

            if org.id not in self._active_orgs:
                return {"node_id": node.id, "result": result_text}

            # 非正常退出 / 软 verify_incomplete 都生成 failure_diagnoser 诊断卡片：
            # - 非正常退出：用于 task_failed/task_terminated 卡片展示根因+建议
            # - 软 verify_incomplete：复用诊断模板生成"提示性"卡片，附在 task_complete
            #   气泡末尾，让用户即使收起时间线也能看到「verify 提示但已通过下属交付完成」
            # 正常退出（normal/ask_user）则跳过以避免热路径无用功。
            diagnosis: dict | None = None
            need_diagnosis = (not is_normal) or is_soft_verify
            if need_diagnosis:
                try:
                    diagnosis = _diagnose_failure(react_trace, exit_reason)
                except Exception as diag_err:
                    logger.debug(f"[OrgRuntime] failure diagnosis failed on {node.id}: {diag_err}")
                    diagnosis = None

                # 静默策略（一刀切，2026-04-28 收紧）：
                # verify_incomplete 系列（含 verify_incomplete / verify_incomplete_with_children）
                # 是「verify 规则没匹配」而非「真硬失败」。在「文件已落盘 + 黑板已通知 +
                # 节点输出了完整成果文本」的典型场景下，这类卡片只会让用户困惑
                # 「明明完成了为什么还报错」，纯噪音。
                #
                # 历史窄窗口策略（仅静默「不要附件」+「软 verify」两种）留了
                # 「硬 verify + 用户说了"写一份"被 expects_artifact 命中」的窗口
                # 没堵——典型如 2026-04-28 14:39:37 的 _134209 失败链：主编实际
                # 已经 write_file 4594 字节、blackboard 已通知，verify 仍因为
                # 没调 deliver_artifacts/org_submit_deliverable 而硬判 INCOMPLETE。
                # 用户多次明确反馈「这个卡片对用户很不友好」「早就说不要这个了」。
                #
                # 现策略：root_cause 只要以 "verify_incomplete" 开头就一律 diagnosis=None，
                # 不再依赖 expects_artifact / is_soft_verify 区分窗口。日志保留
                # 一条 INFO 用于事后回溯。event/ws payload 通过下方 `if diagnosis`
                # 分支自动跳过 → UI + 事件双砍（用户选 scope=all 语义）。
                # 真硬失败（loop_terminated / max_iterations / org_delegate_loop 等）
                # 不受影响，保留「为什么失败」卡片。
                rc_for_silence = (diagnosis or {}).get("root_cause") if diagnosis else None
                if diagnosis and rc_for_silence and rc_for_silence.startswith(
                    "verify_incomplete"
                ):
                    logger.info(
                        "[OrgRuntime] silencing verify_incomplete* diagnosis card "
                        "for org=%s node=%s (rc=%s, soft_verify=%s; "
                        "review-hint suppressed from UI + event payload)",
                        org.id, node.id, rc_for_silence, is_soft_verify,
                    )
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
            if is_soft_verify:
                # 软完成路径复用 task_complete 事件，但带上 warning=True 让需要
                # 区分"完美完成"vs"提示性完成"的消费者（评测/统计）能识别。
                # 老前端忽略此字段，行为不变。
                event_payload["warning"] = True
            tool_stats = self._collect_tool_stats_from_trace(react_trace)
            event_payload.update(tool_stats)
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
            if is_soft_verify:
                ws_payload["warning"] = True
            ws_payload.update(tool_stats)
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
                # P0-3：子任务失败/终止时关闭 chain，唤醒所有 org_wait_for_deliverable
                # 等待本 chain 的协程，避免父节点（root）陷入无限轮询死锁。
                if chain_id:
                    try:
                        self._mark_chain_closed(org.id, chain_id)
                        logger.info(
                            "[OrgRuntime] auto-closed chain on failure: "
                            "org=%s chain=%s exit_reason=%s",
                            org.id, chain_id, exit_reason,
                        )
                    except Exception as close_err:
                        logger.debug(
                            "[OrgRuntime] mark_chain_closed on failure error: %s",
                            close_err,
                        )
            elif is_soft_verify:
                root_cause_tag = (diagnosis or {}).get(
                    "root_cause", "verify_incomplete_with_children",
                )
                logger.info(
                    f"[OrgRuntime] Node {node.id} soft-completed: "
                    f"exit_reason={exit_reason}, root_cause={root_cause_tag}; "
                    f"treated as task_completed so post_task_hook can drain parent"
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
                origin = activation_origin or self._pop_root_origin(
                    org.id, node.id, "user_command",
                )
                self._capture_root_visible_result(
                    org.id,
                    node.id,
                    result_text=result_text,
                    origin=origin,
                    is_normal=is_normal,
                    exit_reason=exit_reason,
                )

            # 非正常结束时不触发 post-task hook（避免把"部分/失败结果"再次下发下游）；
            # 软 verify_incomplete 也走 hook，让父级能 drain 子节点交付队列。
            if is_normal:
                asyncio.ensure_future(self._post_task_hook(org, node))

            return_payload: dict = {
                "node_id": node.id,
                "result": result_text,
                "exit_reason": exit_reason,
                **tool_stats,
            }
            if diagnosis:
                return_payload["diagnosis"] = diagnosis
            if is_soft_verify:
                return_payload["soft_complete"] = True
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
            # P0-3：异常路径同样关闭 chain，避免 org_wait_for_deliverable 无限挂起。
            # 此处覆盖 LLM 调用异常 / quota / circuit_breaker 等所有 catch-all 失败。
            if chain_id:
                try:
                    self._mark_chain_closed(org.id, chain_id)
                    logger.info(
                        "[OrgRuntime] auto-closed chain on exception: "
                        "org=%s chain=%s err=%s",
                        org.id, chain_id, str(e)[:80],
                    )
                except Exception as close_err:
                    logger.debug(
                        "[OrgRuntime] mark_chain_closed on exception error: %s",
                        close_err,
                    )
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
            root_intent=self.get_active_root_intent(org.id),
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

        is_root = (node.level == 0 or not org.get_parent(node.id))
        is_coordinator = bool(org.get_children(node.id))

        # 工作台节点（plugin_origin 非空）必须是叶子节点：coordinator 路径会
        # 把 allowed_external 强制清空，导致工作台工具无法放行 → 节点形同虚设。
        # 这里在运行时再保险一次：检测到非法组合时降级为非工作台节点（保留
        # external_tools 但剥离 plugin_origin 标识），并 emit warning，让用户
        # 在前端能看到提示并修复编排。
        active_plugin_origin = dict(node.plugin_origin) if node.plugin_origin else None
        if active_plugin_origin and is_coordinator:
            logger.warning(
                "[OrgRuntime] workbench node %s in org %s has children — "
                "plugin_origin will be ignored at runtime. "
                "Please make workbench nodes leaves.",
                node.id, org.id,
            )
            active_plugin_origin = None

        allowed_external = expand_tool_categories(node.external_tools) - _ORG_CONFLICT_TOOLS
        if is_coordinator:
            # 管理节点只负责拆解、委派、等待和验收；真实文件/浏览器/命令等
            # 外部执行工具留给叶子节点，避免 root/lead 单 Agent 演完整场。
            allowed_external = set()

        # E0-4: 节点级"基础文件工具"开关。即便用户没在 external_tools 里勾选
        # filesystem 类目，只要 enable_file_tools=True（默认），就给节点放行
        # 一组安全的读写工具，避免出现"角色明明该交文件，但提示词里被告知
        # 'write_file 不可用' 只能回纯文本"的死循环。这里刻意不包含 run_shell
        # / delete_file —— 命令执行和删除属高风险，仍要走 external_tools 显式
        # 授权。文件路径在 agent.file_tool.base_path 处被隔离到 org workspace。
        if getattr(node, "enable_file_tools", True) and not is_coordinator:
            allowed_external = allowed_external | {
                "write_file", "read_file", "edit_file", "list_directory",
            }

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

        # 工作台节点：把"插件能力 + 产物交付协议"段拼到 org_context，让 LLM
        # 在 system prompt 中看到具体可调用的工作台工具名与产物自动登记规则。
        # 仅对真正"激活"的工作台节点（active_plugin_origin 非空，即叶子节点）
        # 生效，避免被强制降级的 coordinator 拿到误导性提示。
        if active_plugin_origin:
            try:
                workbench_section = self._build_workbench_prompt_section(
                    agent, active_plugin_origin,
                )
                if workbench_section:
                    org_context_prompt = (
                        f"{org_context_prompt}\n\n{workbench_section}"
                    )
            except Exception:
                logger.debug(
                    "[OrgRuntime] failed to build workbench prompt section",
                    exc_info=True,
                )

        # A node is an "org coordinator" iff it has direct subordinates —
        # ``build_org_node_tools`` already drops ``org_delegate_task`` for
        # leaf nodes, so this aligns the runtime contract: only nodes that
        # *can* delegate are forced into coordinator semantics (must use
        # tools / coordinator-mode prompt). This is the structural identity
        # we use later in ``orchestrator._run_agent_session`` and
        # ``Agent._prepare_session_context`` to keep the editor-in-chief /
        # CEO / tech-lead style nodes from "doing the work themselves"
        # instead of delegating.
        self._override_system_prompt_for_org(agent, org_context_prompt, org_workspace, is_root=is_root)

        agent._org_context = {
            "org_id": org.id,
            "node_id": node.id,
            "tool_handler": self._tool_handler,
            "workspace": org_workspace,
            "is_root": is_root,
            "is_coordinator": is_coordinator,
            "plugin_origin": active_plugin_origin,
        }
        agent._is_org_coordinator = is_coordinator
        agent._workbench_plugin_origin = active_plugin_origin

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
                0, int(getattr(_settings, "force_tool_call_max_retries", 0))
            )

        self._register_org_tool_handler(agent, org.id, node.id)

        return agent

    @staticmethod
    def _build_workbench_prompt_section(
        agent: Any, plugin_origin: dict,
    ) -> str:
        """Compose a "你是【X】工作台节点" section for the system prompt.

        Lists the workbench-specific tools (filtered from
        ``agent.tool_catalog``) and reiterates the artifact-handoff
        contract: workbench produces → runtime auto-registers → submit
        deliverable without re-declaring file_attachments → upstream
        asset_id / image_url flows back into downstream tool input.

        Returns an empty string when the plugin manager / tool catalog
        cannot be queried so the caller leaves the original prompt intact.
        """
        if not plugin_origin:
            return ""
        plugin_id = str(plugin_origin.get("plugin_id") or "").strip()
        if not plugin_id:
            return ""

        pm = getattr(agent, "_plugin_manager", None)
        manifest = None
        plugin_tool_names: set[str] = set()
        if pm is not None:
            try:
                lp = pm.get_loaded(plugin_id)
                if lp is not None:
                    manifest = lp.manifest
                    for t in getattr(lp.api, "_registered_tools", None) or []:
                        if isinstance(t, dict) and t.get("name"):
                            plugin_tool_names.add(t["name"])
            except Exception:
                logger.debug(
                    "[OrgRuntime] _build_workbench_prompt_section: lookup failed",
                    exc_info=True,
                )

        display_name = (
            (getattr(manifest, "display_name_zh", None) or "")
            or (getattr(manifest, "name", None) or "")
            or plugin_id
        )

        active_tool_lines: list[str] = []
        try:
            seen: set[str] = set()
            for t in getattr(agent, "_tools", None) or []:
                if not isinstance(t, dict):
                    continue
                name = t.get("name") or ""
                if name in plugin_tool_names and name not in seen:
                    seen.add(name)
                    desc = (t.get("description") or "").strip()
                    if desc:
                        if len(desc) > 160:
                            desc = desc[:160].rstrip() + "..."
                        active_tool_lines.append(f"- `{name}` — {desc}")
                    else:
                        active_tool_lines.append(f"- `{name}`")
            # 兜底：若 agent._tools 里没找到任何插件工具（节点被错误配置导致
            # external_tools 未声明该工具），把已知工具名列出来让 LLM 看到全貌。
            if not active_tool_lines:
                for name in sorted(plugin_tool_names):
                    active_tool_lines.append(f"- `{name}`")
        except Exception:
            for name in sorted(plugin_tool_names):
                active_tool_lines.append(f"- `{name}`")

        tool_block = "\n".join(active_tool_lines) if active_tool_lines else "（暂无工具）"

        return (
            f"## 你是【{display_name}】工作台节点\n"
            f"专属能力（请按 input_schema 严格调用）：\n{tool_block}\n\n"
            "交付协议：\n"
            "1. 工作台工具调用成功后，组织 runtime 会把产物（图片/视频/音频/"
            "本地文件）自动下载到 org workspace 的 `plugin_assets/<workbench>/<task>/` "
            "目录，并通过 `_register_file_output` 登记为任务附件（黑板 RESOURCE + "
            "ProjectTask + chat UI 附件 chip）；\n"
            "2. 你**不需要**在 `org_submit_deliverable` 的 `file_attachments` "
            "参数里再重复声明这些产物——runtime 会自动把它们随 TASK_DELIVERED "
            "送给委派人；只需在 `deliverable` 文本里说明产出内容（标题、规格、"
            "prompt 摘要、`asset_id` 等关键标识）即可；\n"
            "3. 如果工具返回里出现 `registered_attachments` 字段，那就是 runtime "
            "已经成功登记的本地路径，你可以在 deliverable 文本里直接引用其中的 "
            "`filename`；\n"
            "4. 若上级 prompt 中提供了上游工作台的 `asset_id` 或 `image_url`，"
            "请如实填入对应工具参数（例如 `seedance_create.from_asset_ids` 或 "
            "`content[].image_url`）；不要凭空想象资源；\n"
            "5. 若上级只是问询/讨论（而非真正下单产出），直接用 "
            "`org_submit_deliverable` 提交文字回答，无需调用工作台工具；\n"
            "6. 完成后调 `org_submit_deliverable` 把成果交给委派人，等待验收。"
        )

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
                " + run_powershell 执行 python xxx.py；只有明确需要 bash/Git Bash 语义时才用 run_shell"
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

        parts.append(
            "## 轻量直答与收敛\n"
            "- 用户只是要求一句话/简洁说明你的职责、身份或角色时，直接用 1-2 句话回答；"
            "不要创建计划、不要委派、不要查制度。\n"
            "- 已经有足够事实可回答时立即收敛；只有涉及真实任务状态、文件、日志、"
            "交付验收或外部证据时，才调用对应工具。"
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
        from openakita.agents.profile import AgentProfile, AgentType, SkillsMode

        if node.agent_profile_id:
            try:
                base = self._get_shared_profile(node.agent_profile_id)
                if base:
                    preferred_endpoint = node.preferred_endpoint or base.preferred_endpoint
                    endpoint_policy = node.endpoint_policy if node.preferred_endpoint else base.endpoint_policy
                    profile = base.derive(
                        id=f"org_node_{node.id}",
                        name=node.role_title,
                        type=AgentType.DYNAMIC,
                        custom_prompt=org_prompt,
                        skills=node.skills if node.skills else base.skills,
                        skills_mode=SkillsMode(node.skills_mode) if node.skills_mode != "all" else base.skills_mode,
                        preferred_endpoint=preferred_endpoint,
                        endpoint_policy=endpoint_policy,
                        created_by="org_runtime",
                        ephemeral=True,
                        inherit_from=node.agent_profile_id,
                    )
                    return profile
                logger.warning(
                    "[OrgRuntime] Profile %r for node %s (%s) not found; "
                    "falling back to inline dynamic profile",
                    node.agent_profile_id,
                    node.id,
                    node.role_title,
                )
            except Exception as e:
                logger.warning(f"[OrgRuntime] Failed to load profile {node.agent_profile_id}: {e}")

        if not node.agent_profile_id:
            logger.warning(
                "[OrgRuntime] Node %s (%s) has no agent_profile_id; "
                "falling back to inline dynamic profile",
                node.id,
                node.role_title,
            )

        return AgentProfile(
            id=f"org_node_{node.id}",
            name=node.role_title,
            custom_prompt=org_prompt,
            skills=node.skills,
            skills_mode=SkillsMode(node.skills_mode) if node.skills_mode != "all" else SkillsMode.ALL,
            preferred_endpoint=node.preferred_endpoint,
            endpoint_policy=node.endpoint_policy,
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
            # P0-1：根节点收到 TASK_DELIVERED 必须放行——这是根节点完成"汇总性
            # 终态回复"的唯一触发点。如果继续静默，就出现用户反馈的"最后由
            # 子节点（策划编辑）说话、根节点没有总结"问题：子任务交付后链路
            # 被 close，TASK_DELIVERED 还没到根节点 mailbox 就被这条软屏障
            # 吃掉了。只对根 + TASK_DELIVERED 这一组合放行，避免破坏其它
            # 合法的 close 行为。
            #
            # 2026-04-28 收紧：去重已被 root 主任务 org_accept_deliverable 验收
            # 过的 chain（_root_processed_chains 中），避免同 chain 的多条
            # TASK_DELIVERED 在主任务结束后又触发空跑「补汇总」ReAct（每次约
            # 150K tokens 浪费，详见 _134209 现象）。首次到达仍正常激活。
            try:
                _is_root_for_gate = bool(
                    self.get_org(org_id) and
                    self.get_org(org_id).get_parent(node_id) is None
                )
            except Exception:
                _is_root_for_gate = False
            _root_delivery_bypass = (
                _is_root_for_gate
                and msg.msg_type == MsgType.TASK_DELIVERED
                and not self._is_chain_processed_by_root(org_id, chain_id_peek)
            )
            if closed and msg.msg_type not in (
                MsgType.TASK_ASSIGN,
                MsgType.TASK_REJECTED,
            ) and not _root_delivery_bypass:
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

            logger.debug(
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
            prefix += f" [任务链: {chain_id[:12]}…]"

        extra = ""
        if msg.msg_type == MsgType.TASK_DELIVERED:
            deliverable = msg.metadata.get("deliverable", "")
            summary = msg.metadata.get("summary", "")
            if deliverable:
                extra = f"\n交付内容: {deliverable}"
            if summary:
                extra += f"\n工作简述: {summary}"
            # E0-2: 附件清单一定要显式喂给上级 LLM。否则父节点只看见一段文字，
            # 看不到下属其实交了几个文件、什么名字、什么大小，结果就是验收时
            # 错判（继续追问"你交付的文件呢"）或者打回一份本来已经合格的交付。
            # metadata["file_attachments"] 由 _handle_org_submit_deliverable 写
            # 入，结构是 [{"filename": str, "file_path": str, "file_size": int?}].
            attachments = msg.metadata.get("file_attachments") or []
            if isinstance(attachments, list) and attachments:
                lines = []
                for att in attachments[:20]:
                    if not isinstance(att, dict):
                        continue
                    fname = att.get("filename") or att.get("name") or "(未命名)"
                    fpath = att.get("file_path") or att.get("path") or ""
                    size = att.get("file_size") or att.get("size_bytes") or 0
                    if size and isinstance(size, int):
                        if size >= 1024 * 1024:
                            size_str = f" ({size / 1024 / 1024:.1f} MB)"
                        elif size >= 1024:
                            size_str = f" ({size / 1024:.1f} KB)"
                        else:
                            size_str = f" ({size} B)"
                    else:
                        size_str = ""
                    if fpath:
                        lines.append(f"  - **{fname}**{size_str} → `{fpath}`")
                    else:
                        lines.append(f"  - **{fname}**{size_str}")
                if lines:
                    extra += f"\n附件清单（共 {len(attachments)} 个）:\n" + "\n".join(lines)
            if chain_id:
                extra += (
                    "\n验收时请使用完整任务链 ID："
                    f'task_chain_id="{chain_id}"'
                )
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

    def get_org_snapshot(self, org_id: str) -> Organization | None:
        """Return the authoritative organization snapshot for status readers.

        Running organizations keep live node status in ``_active_orgs``.  API
        and org-awareness tools should read that object first so dashboards do
        not drift behind task execution events.
        """
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
            from datetime import UTC, datetime, timedelta

            store = self.get_event_store(org_id)
            if store is None:
                return False
            cutoff = datetime.now(UTC) - timedelta(
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

    async def set_node_status(
        self,
        org: Organization,
        node: OrgNode,
        new_status: NodeStatus,
        reason: str = "",
        *,
        current_task: str | None = "",
    ) -> None:
        """Set, persist and broadcast one node status change.

        This is the single public helper for externally triggered node status
        updates (for example freeze/unfreeze tools).  It keeps REST snapshots,
        org tools and the live dashboard on the same state source.
        """
        self._set_node_status(org, node, new_status, reason)
        await self._save_org(org)
        await self._broadcast_ws("org:node_status", {
            "org_id": org.id,
            "node_id": node.id,
            "status": new_status.value,
            "current_task": current_task,
        })

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
        # _root_processed_chains 仅服务于「主任务汇总轮内的重复 TASK_DELIVERED
        # 去重」，没有跨 org-restart 的语义意义；deactivate 时直接释放，避免
        # 长跑实例累积。与 _closed_chains 的"保留已关闭链知识"不同。
        self._root_processed_chains.pop(org_id, None)

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
            # P0-1：同样对 root + TASK_DELIVERED 放行，理由见 _on_node_message 的注释。
            # 2026-04-28 收紧：去重已被 root 主任务验收过的 chain，避免空跑「补汇总」轮。
            if suppress_on:
                chain_peek = msg.metadata.get("task_chain_id") if msg.metadata else None
                closed = (
                    bool(msg.metadata and msg.metadata.get("chain_closed"))
                    or self.is_chain_closed(org.id, chain_peek)
                )
                try:
                    _is_root_for_drain = org.get_parent(node.id) is None
                except Exception:
                    _is_root_for_drain = False
                _root_delivery_bypass = (
                    _is_root_for_drain
                    and msg.msg_type == MsgType.TASK_DELIVERED
                    and not self._is_chain_processed_by_root(org.id, chain_peek)
                )
                if closed and msg.msg_type not in (
                    MsgType.TASK_ASSIGN,
                    MsgType.TASK_REJECTED,
                ) and not _root_delivery_bypass:
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

    def get_command_tracker_snapshot(
        self,
        org_id: str,
        command_id: str,
    ) -> dict | None:
        """Return a compact live snapshot for a running user command."""
        if not command_id:
            return None
        now = time.monotonic()
        for (oid, root_id), tracker in list(self._active_user_cmd.items()):
            if oid != org_id or tracker.command_id != command_id:
                continue
            blockers = self._collect_tracker_blockers(tracker)
            return {
                "command_id": tracker.command_id,
                "root_node_id": root_id,
                "phase": tracker._last_phase_emitted or tracker.state or "running",
                "tracker_state": tracker.state,
                "root_chain_id": tracker.root_chain_id or "",
                "open_chains": sorted(tracker.open_chains),
                "open_chain_count": len(tracker.open_chains),
                "open_subtree_chains": blockers.get("open_subtree_chains", []),
                "chain_parent": blockers.get("chain_parent", {}),
                "root_status": blockers.get("root_status", ""),
                "busy_nodes": blockers.get("busy_nodes", []),
                "pending_mailbox": blockers.get("pending_mailbox", []),
                "blockers": blockers.get("blockers", []),
                "blocker_summary": blockers.get("summary", ""),
                "elapsed_s": round(now - tracker.started_at, 1),
                "last_progress_elapsed_s": round(now - tracker.last_progress_at, 1),
                "warned_stuck": tracker.warned_stuck,
                "auto_stopped": tracker.auto_stopped,
                "user_cancelled": tracker.user_cancelled,
            }
        return None

    def _collect_tracker_blockers(self, tracker: UserCommandTracker) -> dict[str, Any]:
        """Return structured reasons why a user command is not finalized yet."""
        org = self.get_org(tracker.org_id)
        bucket = self._closed_chains.get(tracker.org_id) or {}
        subtree = self._collect_chain_subtree(tracker.root_chain_id)
        open_subtree = sorted(cid for cid in subtree if cid not in bucket)
        if not subtree:
            open_subtree = sorted(tracker.open_chains)

        root_status = ""
        busy_nodes: list[dict[str, str]] = []
        pending_mailbox: list[dict[str, Any]] = []
        if org:
            root = org.get_node(tracker.root_node_id)
            root_status = root.status.value if root else "missing"
            for node in org.nodes:
                if node.id != tracker.root_node_id and node.status in (
                    NodeStatus.BUSY, NodeStatus.WAITING,
                ):
                    busy_nodes.append({
                        "node_id": node.id,
                        "role_title": node.role_title,
                        "status": node.status.value,
                    })
            messenger = self.get_messenger(tracker.org_id)
            if messenger:
                for node in org.nodes:
                    count = messenger.get_pending_count(node.id)
                    if count > 0:
                        pending_mailbox.append({
                            "node_id": node.id,
                            "role_title": node.role_title,
                            "pending": count,
                        })

        blockers: list[str] = []
        if open_subtree:
            blockers.append("open_chains")
        if root_status and root_status != NodeStatus.IDLE.value:
            blockers.append("root_not_idle")
        if busy_nodes:
            blockers.append("busy_nodes")
        if pending_mailbox:
            blockers.append("pending_mailbox")
        if tracker.state == "awaiting_summary":
            blockers.append("awaiting_summary")

        summary_parts: list[str] = []
        if open_subtree:
            summary_parts.append(f"仍有 {len(open_subtree)} 条任务链未关闭")
        if root_status and root_status != NodeStatus.IDLE.value:
            summary_parts.append(f"根节点状态为 {root_status}")
        if busy_nodes:
            names = ", ".join(n["role_title"] or n["node_id"] for n in busy_nodes[:3])
            summary_parts.append(f"忙碌节点: {names}")
        if pending_mailbox:
            names = ", ".join(
                f"{n['role_title'] or n['node_id']}({n['pending']})"
                for n in pending_mailbox[:3]
            )
            summary_parts.append(f"待处理消息: {names}")
        if tracker.state == "awaiting_summary":
            summary_parts.append("正在等待根节点生成最终汇总")

        return {
            "blockers": blockers,
            "summary": "；".join(summary_parts),
            "open_subtree_chains": open_subtree,
            "chain_parent": {
                cid: self._chain_parent.get(cid)
                for cid in open_subtree
            },
            "root_status": root_status,
            "busy_nodes": busy_nodes,
            "pending_mailbox": pending_mailbox,
        }

    async def cancel_user_command(
        self,
        org_id: str,
        command_id: str | None = None,
    ) -> dict:
        """用户主动强制终止当前在跑的用户命令。

        语义：
          - 把该 org 下匹配 ``command_id`` 的未完成 ``UserCommandTracker`` 标记为
            ``user_cancelled=True`` + ``auto_stopped=True``，并 ``completed.set()``，
            让 ``send_command`` 立刻 unblock 走"stopped_by_watchdog +
            cancelled_by_user"分支返回阶段性结果。
          - 复用 :meth:`_soft_stop_org` 取消所有 BUSY 节点 / 清 mailbox /
            唤醒 inbox 等待者，但**不会**改变组织 OrgStatus —— 用户可立即
            再发新指令。

        Args:
            org_id: 目标组织。
            command_id: 有值时只终止匹配的用户命令；为空时终止该 org 下所有
                未完成 trackers（保留旧调用兼容）。

        Returns:
            ``{"ok": True, "cancelled_roots": [...], "command_id": ...}``
        """
        cancelled_roots: list[str] = []
        for (oid, root_id), tracker in list(self._active_user_cmd.items()):
            if oid != org_id:
                continue
            if command_id and tracker.command_id != command_id:
                continue
            if tracker.completed.is_set():
                continue
            tracker.user_cancelled = True
            tracker.auto_stopped = True
            tracker.completed.set()
            cancelled_roots.append(root_id)

        try:
            await self._soft_stop_org(org_id)
        except Exception as e:
            logger.warning(
                "[OrgRuntime] cancel_user_command soft_stop failed: %s", e,
            )

        try:
            self.get_event_store(org_id).emit(
                "user_command_cancelled",
                "user",
                {
                    "command_id": command_id,
                    "cancelled_roots": cancelled_roots,
                },
            )
        except Exception:
            pass

        logger.info(
            "[OrgRuntime] cancel_user_command: org=%s cmd=%s roots=%s",
            org_id, command_id, cancelled_roots,
        )
        return {
            "ok": True,
            "command_id": command_id,
            "cancelled_roots": cancelled_roots,
        }

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

    def get_active_root_intent(self, org_id: str) -> str:
        """Return the currently in-flight user command content for *org_id*.

        Used by:
        - ``identity.build_org_context_prompt`` to render "user current order"
          into every node's system prompt while a command is running.
        - ``_handle_org_delegate_task`` to inject "parent task hard boundary"
          into delegated task content when the original user command has
          explicit format/length constraints.

        Returns "" when no command is in flight (zero-effect for callers).
        Picks the first active tracker — in practice an org has at most one
        in-flight user command at a time.
        """
        for (oid, _root_id), tracker in self._active_user_cmd.items():
            if oid == org_id and not tracker.completed.is_set():
                return tracker.user_command_content or ""
        return ""

    def _find_root_node_id(self, org_id: str, node_id: str) -> str | None:
        """Walk up the hierarchy edges to find the root node id for *node_id*.

        Returns *node_id* itself when it's already a root (no parent); None
        when org/node is missing. Used by delegate tool to look up the
        active root intent for the current command tree.
        """
        org = self.get_org(org_id)
        if not org:
            return None
        node = org.get_node(node_id)
        if not node:
            return None
        seen: set[str] = set()
        cur = node
        while cur and cur.id not in seen:
            seen.add(cur.id)
            parent = org.get_parent(cur.id)
            if parent is None or parent.id == cur.id:
                break
            cur = parent
        return cur.id if cur else node_id

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

    # 把 finalize 决策映射到一个面向用户的"阶段"短语，前端用它替代
    # 单调的 "running"，让用户知道是"等汇总"还是真的卡住。这只是展示
    # 层别名，不影响 tracker 状态机。
    _FINALIZE_PHASE_MAP = {
        "subtree_not_closed": "running",
        "active_delegations": "running",
        "root_not_idle": "running",
        "summary_pushed": "awaiting_summary",
        "completed_no_summary": "done",
        "completed_after_summary": "done",
        "completed_legacy": "done",
        "completed_unknown_state": "done",
        "no_org": "done",
    }

    def _log_finalize_decision(
        self,
        tracker: UserCommandTracker,
        decision: str,
        **extra: Any,
    ) -> None:
        """Structured debug log for tracker finalize decisions (L. observability).

        Helps diagnose "why didn't / did this command finish" without trawling
        events.jsonl. DEBUG level: opt-in by lowering openakita.orgs logger.

        Also emits a single ``command_phase`` event to the org event store so
        the HTTP layer can surface a user-friendly phase ("awaiting_summary"
        instead of "running") to the frontend. Same-phase repeats are
        debounced to avoid log spam.
        """
        try:
            subtree = self._collect_chain_subtree(tracker.root_chain_id)
            blockers = self._collect_tracker_blockers(tracker)
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
                "blockers": blockers.get("blockers", []),
                "blocker_summary": blockers.get("summary", ""),
            }
            payload.update(extra)
            logger.debug("[Finalize] %s", payload)
        except Exception:
            logger.debug("[Finalize] log emit failed", exc_info=True)

        try:
            phase = self._FINALIZE_PHASE_MAP.get(decision, "running")
            if phase == tracker._last_phase_emitted:
                return  # 同 phase 不重复发，避免事件流灌爆
            tracker._last_phase_emitted = phase
            self.get_event_store(tracker.org_id).emit(
                "command_phase",
                tracker.root_node_id,
                {
                    "phase": phase,
                    "decision": decision,
                    "command_id": tracker.command_id or "",
                    "root_chain_id": tracker.root_chain_id or "",
                    "blockers": blockers.get("blockers", []),
                    "blocker_summary": blockers.get("summary", ""),
                    "open_subtree_chains": blockers.get("open_subtree_chains", []),
                    "busy_nodes": blockers.get("busy_nodes", []),
                    "pending_mailbox": blockers.get("pending_mailbox", []),
                },
            )
        except Exception:
            logger.debug("[Finalize] phase emit failed", exc_info=True)

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
        # If the root never opened a delegation chain we have nothing to
        # summarise — re-activating it with "[用户指令最终汇总]" would only
        # invite the LLM to hallucinate a recap of work that never happened
        # (see regression: trace 2 in 0939300e0183 where the editor-in-chief
        # fabricated subordinate deliveries because no children were ever
        # delegated). Bail early so ``_maybe_finalize_tracker`` falls back to
        # ``completed_no_summary`` and the user gets the root's first answer.
        if not tracker.root_chain_id:
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
        # P1-6：把允许采纳的 chain_id 白名单显式列出来。
        # _collect_chain_subtree 已经按 root_chain_id 严格做过 BFS，因此 subtree
        # 与本 command_id 严格一一对应；把 chain_id 暴露给 LLM 作为白名单，
        # 避免 root 节点的对话历史里残留的"上一条 command 的 deliverable"
        # 被 LLM 误纳入本次汇总。
        allowed_chain_ids: list[str] = []
        try:
            from openakita.orgs.project_store import ProjectStore as _PS
            store = _PS(self._manager._org_dir(tracker.org_id))
            for cid in list(subtree)[:10]:
                task = store.find_task_by_chain(cid)
                if task:
                    title = (task.title or "")[:60]
                    assignee = task.assignee_node_id or ""
                    recap_parts.append(
                        f"- chain={cid[:12]} {assignee}: {title} [{task.status.value}]"
                    )
                    allowed_chain_ids.append(cid)
                else:
                    allowed_chain_ids.append(cid)
        except Exception:
            logger.debug(
                "[PushSummary] project_store recap failed", exc_info=True,
            )
            allowed_chain_ids = list(subtree)[:10]

        recap = "\n".join(recap_parts) if recap_parts else (
            "（无可识别的子任务记录，请直接根据已收到的下级 deliverable 汇总）"
        )
        whitelist_str = ", ".join(c[:12] for c in allowed_chain_ids) or "（空）"
        body = (
            f"[用户指令最终汇总] 用户指令 {tracker.command_id or '(unknown)'} "
            "所触发的所有委派任务均已关闭。"
            "请基于下级各自交付的成果，向用户输出一份完整的最终汇总——"
            "覆盖每位下级的产出要点、关键文件/链接、已完成程度、"
            "以及任何遗留风险或下一步建议。\n\n"
            "已关闭的子任务概览：\n" + recap + "\n\n"
            f"⚠️ 严格范围约束：本次汇总只能引用属于 task_chain_id ∈ "
            f"[{whitelist_str}] 的下级交付物。"
            "你的对话历史中可能残留着上一条用户指令的 deliverable，"
            "**严禁**把这些历史交付物纳入本次汇总；如不确定 chain 归属，"
            "宁可从摘要中剔除也不要张冠李戴。\n\n"
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
    _ROOT_INCOMPLETE_RESULT_MIN_CHARS = 200

    def _capture_root_visible_result(
        self,
        org_id: str,
        node_id: str,
        *,
        result_text: str | None,
        origin: str,
        is_normal: bool,
        exit_reason: str,
    ) -> dict | None:
        """Cache the best root-node answer that is safe to show to the user.

        `verify_incomplete` can mean the verifier missed a valid final summary.
        When the root has already produced substantial text for a user-visible
        activation, keep that text for the HTTP command result instead of
        falling back to an earlier partial activation.
        """
        if origin not in self._FINAL_RESULT_ORIGINS:
            return None
        text = result_text if isinstance(result_text, str) else ""
        stripped = text.strip()
        if not stripped:
            return None

        payload: dict[str, Any] | None = None
        if is_normal:
            payload = {
                "node_id": node_id,
                "result": text,
                "origin": origin,
            }
        elif (
            exit_reason.startswith("verify_incomplete")
            and len(stripped) >= self._ROOT_INCOMPLETE_RESULT_MIN_CHARS
        ):
            payload = {
                "node_id": node_id,
                "result": text,
                "origin": origin,
                "exit_reason": exit_reason,
                "usable_incomplete": True,
                "warning": (
                    "最终汇总已生成，但任务校验器未确认完成；"
                    "已保留完整汇总内容。"
                ),
            }

        if payload is not None:
            self._latest_root_result[org_id] = payload
        return payload

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

        # 软看门狗只关注连续无真实进展，不限制有持续产出的长任务。
        warn_secs = _cfg("org_command_stuck_warn_secs", 900)
        autostop_secs = _cfg("org_command_stuck_autostop_secs", 3600)
        hard_cap = _cfg("org_command_timeout_secs", 0)

        # 启用时仍维持合理下限与顺序约束（避免用户配出反直觉的极小值）
        if warn_secs > 0:
            warn_secs = max(30, warn_secs)
        if autostop_secs > 0:
            autostop_secs = max(
                (warn_secs + 60) if warn_secs > 0 else 60, autostop_secs
            )

        active = [t for t in (warn_secs, autostop_secs, hard_cap) if t > 0]
        poll_interval = (
            max(5.0, min(30.0, min(active) / 3.0)) if active else 30.0
        )

        try:
            while not tracker.completed.is_set():
                try:
                    await asyncio.wait_for(
                        tracker.completed.wait(), timeout=poll_interval,
                    )
                    return
                except TimeoutError:
                    pass

                if tracker.completed.is_set():
                    return

                now = time.monotonic()
                idle = now - tracker.last_progress_at

                if (
                    warn_secs > 0
                    and idle >= warn_secs
                    and not tracker.warned_stuck
                ):
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

                should_autostop = autostop_secs > 0 and idle >= autostop_secs
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
                        self.get_event_store(tracker.org_id).emit(
                            "command_stopped_no_progress",
                            tracker.root_node_id,
                            {
                                "command_id": tracker.command_id or "",
                                "idle_secs": int(idle),
                                "open_chains": list(tracker.open_chains),
                            },
                        )
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
            from openakita.orgs.event_router import route_org_event
            await route_org_event(event, data)
        except Exception:
            logger.debug("[OrgRuntime] route_org_event failed: %s %s", event, data, exc_info=True)
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
            is_plugin_tool = self._is_plugin_tool(agent, tool_name)
            if is_plugin_tool:
                await self._broadcast_ws("org:workbench_tool_status", {
                    "org_id": org_id,
                    "node_id": node_id,
                    "tool_name": tool_name,
                    "status": "running",
                })
                self.get_event_store(org_id).emit(
                    "workbench_tool_started",
                    node_id,
                    {"tool_name": tool_name, "input": str(tool_input)[:_LIM_EVENT]},
                )
            try:
                result = await original_with_policy(
                    tool_name, tool_input, policy_result, session_id=session_id,
                )
            except Exception as exc:
                if is_plugin_tool:
                    await self._broadcast_ws("org:workbench_tool_status", {
                        "org_id": org_id,
                        "node_id": node_id,
                        "tool_name": tool_name,
                        "status": "failed",
                        "error": str(exc)[:_LIM_WS],
                    })
                    self.get_event_store(org_id).emit(
                        "workbench_tool_failed",
                        node_id,
                        {"tool_name": tool_name, "error": str(exc)[:_LIM_EVENT]},
                    )
                raise
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
            elif is_plugin_tool:
                # 工作台节点：插件工具产出（image_urls / video_url / local_paths /
                # asset_ids）由 runtime 自动下载到 org workspace 并走 _register_file_output
                # 登记，让产物进入 chat UI 附件 chip + 任务验收链路。
                plugin_ok = True
                try:
                    import json as _json
                    parsed = _json.loads(result) if isinstance(result, str) else None
                    if isinstance(parsed, dict) and parsed.get("ok") is False:
                        plugin_ok = False
                except Exception:
                    plugin_ok = True
                try:
                    ws = getattr(agent, "_org_context", {}).get("workspace")
                    enhanced = await self._record_plugin_asset_output(
                        agent, org_id, node_id, tool_name, tool_input, result,
                        workspace=ws,
                    )
                    if enhanced is not None:
                        result = enhanced
                except Exception:
                    logger.debug(
                        "[OrgRuntime] failed to record plugin asset output",
                        exc_info=True,
                    )
                if plugin_ok:
                    await self._broadcast_ws("org:workbench_tool_status", {
                        "org_id": org_id,
                        "node_id": node_id,
                        "tool_name": tool_name,
                        "status": "finished",
                    })
                    self.get_event_store(org_id).emit(
                        "workbench_tool_succeeded",
                        node_id,
                        {"tool_name": tool_name},
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

        # E0-3: 拒绝把空文件登记成"产出"。空文件几乎只可能是 LLM 调用
        # write_file 写入空字符串、或者插件创建占位文件还没写入数据时，被
        # 误识别为"已交付"。一旦空文件混入黑板/ProjectTask，下游验收会以为
        # 任务已经完成，导致整条任务被错判为成功。这里只看物理大小，未来
        # 如果要做"语义为空检测"放在更上层。
        try:
            size_bytes = p.stat().st_size
        except OSError:
            return None
        if size_bytes <= 0:
            logger.info(
                "[OrgRuntime] _register_file_output skip empty file: %s (org=%s node=%s)",
                str(p), org_id, node_id,
            )
            return None
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

        # per-task 文件计数器 +1：auto-persist 兜底仅在本任务零文件时触发，
        # 计数器是判定"LLM 是否已自己产出文件"的唯一信号源。计数失败不能
        # 影响主流程，所以包在 try 里。
        try:
            counter_key = f"{org_id}:{node_id}"
            self._node_files_registered_in_task[counter_key] = (
                self._node_files_registered_in_task.get(counter_key, 0) + 1
            )
        except Exception:
            pass

        return {
            "filename": resolved_name,
            "file_path": str(p),
            "file_size": size_bytes,
        }

    @staticmethod
    def _react_trace_has_tool(
        react_trace: list[dict] | None, tool_name: str
    ) -> bool:
        """扫一遍最近一次 ReAct trace，判断指定工具是否真的被调用过。

        用于 auto-persist 后的"是否需要合成 TASK_DELIVERED"决策——LLM 自己
        已经走过 ``org_submit_deliverable`` 时，再合成一遍会和 messenger 5s
        内容 hash 去重碰撞或重复唤醒父级。trace 缺失/异常时保守返回 False
        （让上游有机会触发兜底）。
        """
        if not react_trace or not tool_name:
            return False
        try:
            for iter_entry in react_trace:
                if not isinstance(iter_entry, dict):
                    continue
                for tc in iter_entry.get("tool_calls") or ():
                    if isinstance(tc, dict) and tc.get("name") == tool_name:
                        return True
        except Exception:
            return False
        return False

    @staticmethod
    def _collect_tool_stats_from_trace(react_trace: list[dict] | None) -> dict:
        """Return compact tool usage stats for org timeline/UI events."""
        if not react_trace:
            return {"tools_used": [], "tools_total": 0}
        tools: list[dict] = []
        total = 0
        try:
            for iter_entry in react_trace:
                if not isinstance(iter_entry, dict):
                    continue
                results_by_id: dict[str, dict] = {}
                for r in iter_entry.get("tool_results") or ():
                    if isinstance(r, dict):
                        rid = str(r.get("tool_use_id") or r.get("id") or "")
                        if rid:
                            results_by_id[rid] = r
                for call in iter_entry.get("tool_calls") or ():
                    if not isinstance(call, dict):
                        continue
                    name = str(call.get("name") or "")
                    if not name:
                        continue
                    total += 1
                    result = results_by_id.get(str(call.get("id") or ""), {})
                    tools.append({
                        "name": name,
                        "success": not bool(result.get("is_error")),
                    })
        except Exception:
            return {"tools_used": [], "tools_total": 0}
        return {"tools_used": tools[:20], "tools_total": total}

    @staticmethod
    def _extract_accepted_chain_ids(
        react_trace: list[dict] | None,
    ) -> list[str]:
        """扫一遍 ReAct trace，抽出所有「成功的」 ``org_accept_deliverable``
        调用所验收的 ``task_chain_id`` 列表。

        判定方式与 :func:`failure_diagnoser._has_accepted_child_signal` 对齐：
        - 工具名 == ``org_accept_deliverable``
        - tool_results 中对应 ``tool_use_id`` 的条目 ``is_error`` 为 False
        - 且 result_content 不含 "_is_error_entry" 失败 marker（这里简化为
          只看 is_error 标志位 + 非 ``ok=false`` 的 JSON 标记）

        结果用于 root 节点 task_completed 时登记到 ``_root_processed_chains``，
        让后续 mailbox 残留的同 chain TASK_DELIVERED 不再触发重复 ReAct。

        trace 缺失/异常一律返回空列表（保守策略，宁可不去重也不误吞）。
        """
        if not react_trace:
            return []
        accepted: list[str] = []
        try:
            for iter_entry in react_trace:
                if not isinstance(iter_entry, dict):
                    continue
                results_by_id: dict[str, dict] = {}
                for r in (iter_entry.get("tool_results") or ()):
                    if isinstance(r, dict):
                        rid = r.get("tool_use_id") or r.get("id") or ""
                        if rid:
                            results_by_id[rid] = r
                for call in iter_entry.get("tool_calls") or ():
                    if not isinstance(call, dict):
                        continue
                    if str(call.get("name") or "") != "org_accept_deliverable":
                        continue
                    tool_id = call.get("id") or ""
                    res = results_by_id.get(tool_id, {}) if tool_id else {}
                    if bool(res.get("is_error")):
                        continue
                    res_text = str(res.get("result_content") or "")
                    # tool_handler 失败时直接返回中文短句（"组织未运行..." 等）
                    # 而不是 JSON；只要 result 不是「以 { 开头的成功 JSON」就保守跳过。
                    rt = res_text.strip()
                    if rt.startswith("{"):
                        if '"ok": false' in rt or '"ok":false' in rt:
                            continue
                    elif rt:
                        # 非 JSON 失败短句（"组织未运行" / "缺少 from_node" / "不能验收..."）
                        # 一律视为未真正 accept。
                        continue
                    inp = call.get("input") if isinstance(call.get("input"), dict) else {}
                    chain_id = str(inp.get("task_chain_id") or "").strip()
                    if chain_id:
                        accepted.append(chain_id)
        except Exception:
            return []
        return accepted

    async def _synthesize_task_delivered_to_parent(
        self,
        *,
        org: Organization,
        from_node: OrgNode,
        chain_id: str,
        deliverable_text: str,
        attachment: dict,
    ) -> bool:
        """子节点 auto-persist 后给父节点合成一条 ``TASK_DELIVERED``。

        仅在 ``_activate_and_run_inner`` 走 auto-persist 且 LLM 整轮没自己调
        ``org_submit_deliverable`` 时被触发。复用 ``_handle_org_submit_deliverable``
        同款 ``OrgMessage(TASK_DELIVERED)`` 结构 + ``messenger.send`` 路径，
        让父级 mailbox / wait_for_deliverable / 项目状态 / `org:task_delivered`
        WS 广播全部正常闭环。

        返回 True 表示消息已被 messenger 接收。任何失败都吞掉并 warning。
        """
        try:
            parent = org.get_parent(from_node.id)
        except Exception:
            parent = None
        if parent is None:
            return False
        messenger = self.get_messenger(org.id)
        if messenger is None:
            return False

        body = (deliverable_text or "").strip()
        summary = body[:200]
        metadata: dict = {
            "deliverable": body[:2000],
            "summary": summary[:500],
            "task_chain_id": chain_id,
            "auto_synthesized": True,
            "file_attachments": [attachment],
        }
        msg = OrgMessage(
            org_id=org.id,
            from_node=from_node.id,
            to_node=parent.id,
            msg_type=MsgType.TASK_DELIVERED,
            content=f"任务交付（兜底落盘）: {body[:_LIM_EVENT]}",
            metadata=metadata,
        )

        try:
            ok = await messenger.send(msg)
        except Exception:
            logger.warning(
                "[OrgRuntime] synthetic TASK_DELIVERED messenger.send failed",
                exc_info=True,
            )
            return False
        if not ok:
            logger.info(
                "[OrgRuntime] synthetic TASK_DELIVERED dropped by messenger "
                "(dedupe/bandwidth/target-not-found): org=%s from=%s to=%s chain=%s",
                org.id, from_node.id, parent.id, chain_id,
            )
            return False

        try:
            self.get_event_store(org.id).emit(
                "task_delivered", from_node.id,
                {
                    "to": parent.id,
                    "chain_id": chain_id,
                    "deliverable_preview": body[:_LIM_EVENT],
                    "file_count": 1,
                    "auto_synthesized": True,
                },
            )
        except Exception:
            pass
        try:
            await self._broadcast_ws("org:task_delivered", {
                "org_id": org.id,
                "from_node": from_node.id,
                "to_node": parent.id,
                "chain_id": chain_id,
                "summary": summary[:_LIM_WS],
                "auto_synthesized": True,
            })
        except Exception:
            pass
        try:
            self._tool_handler._link_project_task(
                org.id, chain_id,
                status="delivered",
                deliverable_content=body[:2000],
                delivery_summary=summary[:500],
            )
        except Exception:
            pass
        try:
            self._on_inbound_for_node(org.id, parent.id)
        except Exception:
            pass
        logger.info(
            "[OrgRuntime] synthesized TASK_DELIVERED: org=%s from=%s to=%s "
            "chain=%s file=%s",
            org.id, from_node.id, parent.id, chain_id,
            attachment.get("filename"),
        )
        return True

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

    # ------------------------------------------------------------------
    # Workbench (plugin) node asset bridging
    # ------------------------------------------------------------------

    # 单个文件最大下载尺寸（防止恶意 URL 拖垮节点磁盘）。50 MB 与
    # seedance-video 上传体积上限一致。
    _PLUGIN_ASSET_MAX_BYTES: int = 50 * 1024 * 1024
    _PLUGIN_ASSET_DOWNLOAD_TIMEOUT_S: float = 120.0

    def _is_plugin_tool(self, agent: Any, tool_name: str) -> bool:
        """Return True iff ``tool_name`` was registered by a loaded plugin
        on the given agent's PluginManager.

        Caches the per-agent plugin tool name set on ``agent._plugin_tool_names``
        so the lookup is O(1) per call. Re-computation only happens when the
        attribute is missing — at which point we walk
        ``agent._plugin_manager.loaded_plugins.values()`` once.
        """
        if not tool_name or tool_name.startswith("org_"):
            return False
        cached: set[str] | None = getattr(agent, "_plugin_tool_names", None)
        if cached is None:
            cached = set()
            pm = getattr(agent, "_plugin_manager", None)
            if pm is not None:
                try:
                    for lp in pm.loaded_plugins.values():
                        for t in getattr(lp.api, "_registered_tools", None) or []:
                            n = t.get("name") if isinstance(t, dict) else None
                            if n:
                                cached.add(n)
                except Exception:
                    logger.debug(
                        "[OrgRuntime] failed to enumerate plugin tool names",
                        exc_info=True,
                    )
            agent._plugin_tool_names = cached
        return tool_name in cached

    @staticmethod
    def _plugin_id_for_tool(agent: Any, tool_name: str) -> str:
        """Best-effort lookup of which plugin registered ``tool_name``.

        Returns the plugin id, or an empty string if the mapping cannot be
        established (e.g. PluginManager not attached). The result is only
        used for namespacing the on-disk asset directory.
        """
        pm = getattr(agent, "_plugin_manager", None)
        if pm is None:
            return ""
        try:
            for lp in pm.loaded_plugins.values():
                for t in getattr(lp.api, "_registered_tools", None) or []:
                    if isinstance(t, dict) and t.get("name") == tool_name:
                        return lp.manifest.id
        except Exception:
            return ""
        return ""

    @staticmethod
    def _safe_asset_filename(raw: str, default_ext: str = ".bin") -> str:
        """Strip path separators and dangerous chars from a candidate filename.

        We never trust an LLM- or plugin-supplied filename to be safe;
        always resolve under a known parent and reject path traversal.
        """
        import re

        cleaned = (raw or "").strip().replace("\\", "/").split("/")[-1]
        cleaned = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", cleaned)
        if not cleaned:
            cleaned = f"asset{default_ext}"
        # cap length so freakishly long filenames cannot blow up file systems
        if len(cleaned) > 120:
            stem, dot, ext = cleaned.rpartition(".")
            if dot and len(ext) <= 8:
                cleaned = stem[: 120 - len(ext) - 1] + "." + ext
            else:
                cleaned = cleaned[:120]
        return cleaned

    @staticmethod
    def _ext_for_url(url: str, fallback: str = ".bin") -> str:
        """Pick a file extension based on URL path (last `.xxx`)."""
        import re
        from urllib.parse import urlparse

        try:
            path = urlparse(url).path
        except Exception:
            return fallback
        m = re.search(r"\.([A-Za-z0-9]{1,8})$", path or "")
        if not m:
            return fallback
        return "." + m.group(1).lower()

    async def _download_to_workspace(
        self,
        url: str,
        dest: Path,
    ) -> bool:
        """Stream-download ``url`` into ``dest`` using httpx, with size cap.

        Returns True on success, False on any failure (network error, 4xx/5xx,
        oversize, IO error). Never raises.
        """
        if not url:
            return False
        try:
            import httpx
        except Exception:
            logger.debug("[OrgRuntime] httpx unavailable, cannot download %s", url)
            return False

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        total = 0
        try:
            async with (
                httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=self._PLUGIN_ASSET_DOWNLOAD_TIMEOUT_S,
                ) as client,
                client.stream("GET", url) as resp,
            ):
                if resp.status_code != 200:
                    logger.info(
                        "[OrgRuntime] download %s returned HTTP %s",
                        url, resp.status_code,
                    )
                    return False
                with open(tmp, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > self._PLUGIN_ASSET_MAX_BYTES:
                            logger.warning(
                                "[OrgRuntime] download %s aborted, "
                                "exceeded %d bytes",
                                url, self._PLUGIN_ASSET_MAX_BYTES,
                            )
                            tmp.unlink(missing_ok=True)
                            return False
                        fh.write(chunk)
            import os as _os
            _os.replace(str(tmp), str(dest))
            return dest.exists() and dest.stat().st_size > 0
        except Exception as exc:
            logger.info(
                "[OrgRuntime] download %s failed: %s", url, exc,
            )
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    @staticmethod
    def _copy_to_workspace(src: Path, dest: Path) -> bool:
        """Hard-link or copy a local file into ``dest``.

        Hard-link first (cheap, atomic on same volume); fall back to copy
        when hard-linking is not possible (e.g. across volumes on Windows).
        Returns True on success.
        """
        import shutil

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                return True
            try:
                import os as _os
                _os.link(str(src), str(dest))
                return True
            except OSError:
                shutil.copy2(str(src), str(dest))
                return True
        except Exception as exc:
            logger.debug(
                "[OrgRuntime] copy %s -> %s failed: %s", src, dest, exc,
            )
            return False

    async def _record_plugin_asset_output(
        self,
        agent: Any,
        org_id: str,
        node_id: str,
        tool_name: str,
        tool_input: dict,
        result: str,
        *,
        workspace: Path | None,
    ) -> str | None:
        """Parse a workbench tool's JSON return value, materialise any
        produced artifacts into the org workspace, register them as task
        attachments, and return a possibly-augmented result string carrying
        a ``registered_attachments`` field so the LLM can see what was
        attached on the next ReAct turn.

        Returns:
          - ``None`` if no enhancement was applied (result not JSON, no
            artifact references, plugin manager unavailable, or workspace
            missing). Caller should keep the original result untouched.
          - a JSON-encoded string with ``registered_attachments`` appended,
            otherwise. The original payload keys are preserved.
        """
        import json as _json

        if not result or workspace is None:
            return None

        try:
            payload = _json.loads(result)
        except (_json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        # Skip failed tool calls — only auto-register on success-looking
        # payloads. Plugins are free to set ok=False; we still respect that.
        if payload.get("ok") is False:
            failure = {
                "tool_name": tool_name,
                "error": (
                    payload.get("error")
                    or payload.get("error_message")
                    or payload.get("message")
                    or "插件工具调用失败"
                ),
                "status_code": payload.get("status_code"),
                "terminal": bool(payload.get("terminal", False)),
            }
            try:
                self.get_event_store(org_id).emit(
                    "workbench_tool_failed",
                    node_id,
                    {
                        "tool_name": tool_name,
                        "error": str(failure["error"])[:_LIM_EVENT],
                        "status_code": failure.get("status_code"),
                        "terminal": failure["terminal"],
                    },
                )
                await self._broadcast_ws("org:workbench_tool_status", {
                    "org_id": org_id,
                    "node_id": node_id,
                    "tool_name": tool_name,
                    "status": "failed",
                    "error": str(failure["error"])[:_LIM_WS],
                    "terminal": failure["terminal"],
                })
            except Exception:
                logger.debug("[OrgRuntime] plugin failure emit failed", exc_info=True)
            if failure["terminal"]:
                self._node_plugin_failures_in_task[f"{org_id}:{node_id}"] = failure
            return None

        plugin_id = self._plugin_id_for_tool(agent, tool_name) or "plugin"
        task_id = (
            payload.get("task_id")
            or tool_input.get("task_id")
            or "task"
        )
        # path-traversal safety: only use the basename of each component
        plugin_slug = self._safe_asset_filename(str(plugin_id), default_ext="")
        task_slug = self._safe_asset_filename(str(task_id), default_ext="")
        target_dir = (
            Path(workspace) / "plugin_assets" / (plugin_slug or "plugin") / (task_slug or "task")
        ).resolve()

        # 仅当目标目录确实位于 workspace 内才放行（即便上面 _safe_asset_filename
        # 已剥离分隔符，做一次显式校验确保万一拼接出绝对路径也能拦下来）。
        try:
            workspace_resolved = Path(workspace).resolve()
            if not str(target_dir).startswith(str(workspace_resolved)):
                logger.warning(
                    "[OrgRuntime] refusing plugin asset dir outside workspace: %s",
                    target_dir,
                )
                return None
        except Exception:
            return None

        chain_id = self.get_current_chain_id(org_id, node_id)

        # 候选附件来源（按优先级合并）：
        #   1. local_paths：插件已经在本地有文件，直接 hardlink / copy
        #   2. asset_ids：通过 Asset Bus 查 source_path / preview_url
        #   3. image_urls / video_url：远端 URL，httpx 流式下载
        candidates: list[dict] = []

        for raw in payload.get("local_paths") or []:
            if isinstance(raw, str) and raw:
                candidates.append({"kind": "local", "src": raw})
        # video_path / image_path 也按本地路径处理（plugin 可能用单数字段）
        for key in ("video_path", "image_path"):
            v = payload.get(key)
            if isinstance(v, str) and v:
                candidates.append({"kind": "local", "src": v})

        for raw in payload.get("asset_ids") or []:
            if isinstance(raw, str) and raw:
                candidates.append({"kind": "asset", "asset_id": raw})

        for raw in payload.get("image_urls") or []:
            if isinstance(raw, str) and raw and not raw.startswith("data:"):
                candidates.append({"kind": "url", "url": raw, "ext_hint": ".png"})
        v = payload.get("video_url")
        if isinstance(v, str) and v and not v.startswith("data:"):
            candidates.append({"kind": "url", "url": v, "ext_hint": ".mp4"})
        v = payload.get("audio_url")
        if isinstance(v, str) and v and not v.startswith("data:"):
            candidates.append({"kind": "url", "url": v, "ext_hint": ".mp3"})

        if not candidates:
            return None

        # 对 asset_ids 通过 PluginManager 的 asset_bus 资源拿 source_path / preview_url
        asset_bus = None
        pm = getattr(agent, "_plugin_manager", None)
        if pm is not None:
            try:
                asset_bus = pm.host_refs.get("asset_bus") if hasattr(pm, "host_refs") else None
                if asset_bus is None:
                    # _host_refs 是 _LiveFilteredHostRefs（dict-like）
                    asset_bus = (
                        pm._external_host_refs.get("asset_bus")
                        if hasattr(pm, "_external_host_refs") else None
                    )
            except Exception:
                asset_bus = None

        registered: list[dict] = []
        seen_paths: set[str] = set()

        for idx, cand in enumerate(candidates):
            try:
                kind = cand["kind"]
                local_path: Path | None = None
                if kind == "local":
                    src_str = cand["src"]
                    src = Path(src_str)
                    if not src.is_absolute():
                        src = (Path(workspace) / src).resolve()
                    if not src.exists() or not src.is_file():
                        continue
                    fname = self._safe_asset_filename(src.name)
                    dest = (target_dir / fname).resolve()
                    if not str(dest).startswith(str(workspace_resolved)):
                        continue
                    if dest != src and not self._copy_to_workspace(src, dest):
                        continue
                    local_path = dest if dest.exists() else src
                elif kind == "asset" and asset_bus is not None:
                    aid = cand["asset_id"]
                    row = None
                    try:
                        row = await asset_bus.get(aid, requester_plugin_id=plugin_id)
                    except Exception:
                        logger.debug(
                            "[OrgRuntime] asset_bus.get(%s) failed", aid, exc_info=True
                        )
                    if not row:
                        continue
                    sp = row.get("source_path")
                    pu = row.get("preview_url")
                    ext = ".bin"
                    if sp:
                        ext = Path(sp).suffix or ext
                    elif pu:
                        ext = self._ext_for_url(pu, fallback=ext)
                    fname = self._safe_asset_filename(f"{aid}{ext}")
                    dest = (target_dir / fname).resolve()
                    if not str(dest).startswith(str(workspace_resolved)):
                        continue
                    if sp:
                        src = Path(sp)
                        if src.exists() and src.is_file():
                            if dest != src and not self._copy_to_workspace(src, dest):
                                continue
                            local_path = dest if dest.exists() else src
                    if local_path is None and pu:
                        ok = await self._download_to_workspace(pu, dest)
                        if ok:
                            local_path = dest
                elif kind == "url":
                    url = cand["url"]
                    ext = self._ext_for_url(url, fallback=cand.get("ext_hint", ".bin"))
                    fname = self._safe_asset_filename(f"{task_slug or 'item'}_{idx}{ext}")
                    dest = (target_dir / fname).resolve()
                    if not str(dest).startswith(str(workspace_resolved)):
                        continue
                    ok = await self._download_to_workspace(url, dest)
                    if ok:
                        local_path = dest
                else:
                    continue

                if local_path is None:
                    continue
                key = str(local_path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)

                att = self._register_file_output(
                    org_id, node_id,
                    chain_id=chain_id,
                    filename=local_path.name,
                    file_path=str(local_path),
                    workspace=Path(workspace),
                )
                if att:
                    registered.append(att)
                    # 累计到本任务的 plugin-hook 附件缓冲，供 submit_deliverable
                    # 在 LLM 未声明 file_attachments 时自动取用。
                    buf = self._node_plugin_attachments_in_task.setdefault(
                        f"{org_id}:{node_id}", []
                    )
                    if not any(
                        b.get("file_path") == att.get("file_path") for b in buf
                    ):
                        buf.append(dict(att))
            except Exception:
                logger.debug(
                    "[OrgRuntime] plugin asset candidate failed: %s",
                    cand, exc_info=True,
                )
                continue

        if not registered:
            return None

        # 把已登记附件回写 payload，让 LLM 在下一轮 ReAct 中看到具体路径，
        # 方便它在 org_submit_deliverable 的 deliverable 文本中按文件名引用。
        payload["registered_attachments"] = registered
        # Mirror to a stable top-level array of paths for convenience
        payload.setdefault("local_paths", []).extend(
            [a["file_path"] for a in registered
             if a.get("file_path") not in (payload.get("local_paths") or [])]
        )
        try:
            return _json.dumps(payload, ensure_ascii=False)
        except Exception:
            return None

