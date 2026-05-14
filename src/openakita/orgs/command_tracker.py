"""User-command lifecycle tracker.

Lifted from :mod:`openakita.orgs.runtime` so the file can be unit-tested in
isolation and so the runtime module shrinks below the 6 k-line mark. The
public surface is preserved verbatim — :class:`UserCommandTracker` is also
re-exported by ``openakita.orgs.runtime`` for backward compatibility, so any
``from openakita.orgs.runtime import UserCommandTracker`` import keeps
working.

Why the tracker is its own module
---------------------------------
- It carries pure in-memory state, no I/O, no dependency on OrgRuntime
  internals (the runtime mutates the public attributes directly).
- The watchdog (``OrgRuntime._command_watchdog``) and the finalizer
  (``OrgRuntime._maybe_finalize_tracker``) read/write the same handful of
  fields here, but neither needs the rest of the 6 000-line runtime to
  reason about a single command's progress.
"""

from __future__ import annotations

import asyncio
import time

__all__ = ["UserCommandTracker"]


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

    This tracker is event-driven: ``register_chain`` / ``unregister_chain`` are
    called from ``_handle_org_delegate_task`` and ``_mark_chain_closed``
    respectively. Completion is signalled via :pyattr:`completed`.

    The :pyattr:`last_progress_at` timestamp is refreshed by ``_touch()``
    whenever any progress signal fires (node status change, org tool call,
    messenger dispatch, chain event, plugin tool start/finish). It is consumed
    **only** by the command watchdog to decide whether to emit a stuck
    warning, soft-stop the organization, or trigger the deadlock early-stop
    path. It does **not** participate in completion judgement.

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
        # 死锁早停的来源标记：True 表示由 deadlock 检测路径触发（全员 IDLE +
        # 仍有未关闭 chain），与"无进度兜底"是两套不同的早停语义。
        "deadlock_stopped",
        "state",
        "summary_pushed_at",
        # BUG-3：保存当前命令的用户原始指令内容，供子节点 system prompt
        # 渲染（identity.build_org_context_prompt）和 _handle_org_delegate_task
        # 注入"父任务硬边界"时取用。命令结束时 tracker 一并被 pop，自动失效。
        "user_command_content",
        # BUG-5：finalize phase 事件去重，避免同 phase 重复 emit。
        "_last_phase_emitted",
        # 死锁早停判定窗口：进入"看似空跑"状态的起始时间戳。条件不再满足
        # 时被清零；持续时间 ≥ deadlock_grace_secs 时触发早停。
        "_quiet_deadlock_since",
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
        self.deadlock_stopped: bool = False
        # See class docstring for state machine details.
        self.state: str = "running"
        # monotonic time when summary inbox push happened (debounce).
        self.summary_pushed_at: float = 0.0
        self.user_command_content: str = user_command_content or ""
        self._last_phase_emitted: str | None = None
        self._quiet_deadlock_since: float = 0.0

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
