"""C12 §14.5 — pending_approvals 持久化层.

设计目标
========

无人值守路径 (scheduler / spawn / webhook) 上的工具调用遇到 ``CONFIRM`` /
``DEFER`` 时, 不能像 attended 路径那样阻塞等待用户响应——任务必须暂停,
把决策点持久化, 让 owner 通过 IM 卡片 / web UI / inbox 收到通知, 之后批准/
拒绝/超时, scheduler 再用 30s replay 策略重跑.

本模块提供:

- ``PendingApproval`` dataclass: 一条待审批记录的不可变快照
- ``PendingApprovalsStore``: load / create / list / get / resolve / expire 的
  持久化层. 支持单进程内多 caller (scheduler + IM gateway + web API + agent)
  并发读写, 使用粗粒度 ``threading.RLock`` + 原子写文件.

落盘文件
========

- 主存储: ``data/scheduler/pending_approvals.json`` —— 当前活跃 + 已 resolve
  但未归档的 entries (status ∈ {PENDING, APPROVED, DENIED, EXPIRED}).
- 归档: ``data/scheduler/pending_approvals_archive_YYYYMM.jsonl`` ——
  resolve 超过 7 天后从主存储移到月度 jsonl, 不再载入内存.

持久化策略
==========

- 写: 全量序列化主 JSON + 原子 rename (写到 .tmp 再 ``os.replace``), 同时
  按需 append 到月度 archive jsonl.
- 读: 启动 lazy load 主 JSON; archive 不入内存, 只在显式 query 时按月文件
  扫描 (低频操作).
- 缺失/损坏的主 JSON 视为空 store, 启动后 WARN 但不阻塞 (保护启动序).

SSE 事件 (C9c)
==============

create() 后 emit ``pending_approval_created``;
resolve() 后 emit ``pending_approval_resolved``.
事件透传由 sse_bus 提供, 本模块只负责调用 hook (避免反向依赖 reasoning_engine).

线程安全
========

所有 state-mutation 方法持 ``self._lock`` (RLock 允许同 thread 重入,
e.g. resolve() 内调 _persist() 内调 _archive_old()).

为什么不放 SQLite?
==================

OpenAkita 主存储已经是 jsonl + json 文件 (sessions / scheduler / memory).
pending_approvals 量级低 (大概率 < 100 条 active), JSON 可读易备份, 与
现有运维节奏一致. 真要换 SQLite 也是统一迁移, 不在本 commit 范围.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

logger = logging.getLogger(__name__)

# Status state machine:
#   PENDING --(resolve allow)--> APPROVED --(7d)--> archived
#   PENDING --(resolve deny)---> DENIED   --(7d)--> archived
#   PENDING --(timeout/expire)-> EXPIRED  --(7d)--> archived
PendingApprovalStatus = Literal["pending", "approved", "denied", "expired"]

DEFAULT_TTL_SECONDS = 24 * 3600  # 24h before EXPIRED if owner ignores
ARCHIVE_AFTER_SECONDS = 7 * 24 * 3600  # archive 7 days post-resolve


@dataclass
class PendingApproval:
    """一条待审批记录.

    JSON-serializable. ``params`` / ``decision_meta`` 由调用方保证为
    json-friendly 类型 (dict / str / int / bool / list / None) — Store 持久化
    时不做深 walk, 类型错误会在 ``json.dumps`` 时显式抛.
    """

    id: str
    task_id: str | None  # scheduled task id, None for ad-hoc spawn / webhook
    session_id: str
    tool_name: str
    params: dict[str, Any]
    approval_class: str | None
    decision_chain: list[dict[str, Any]]  # PolicyDecisionV2.chain serialized
    decision_meta: dict[str, Any]  # PolicyDecisionV2.metadata
    reason: str
    unattended_strategy: str  # "defer_to_owner" / "defer_to_inbox"
    created_at: float
    expires_at: float
    status: PendingApprovalStatus = "pending"
    resolved_at: float | None = None
    resolved_by: str | None = None  # owner user_id who resolved
    resolution: str | None = None  # "allow" / "deny"
    note: str = ""
    # C12 §14.7 (R3-5): captured at creation time so the resume API can
    # write a ReplayAuthorization that engine step 7 can match by
    # user_message equality. Optional for forward/back-compat with old
    # entries on disk (from_dict ignores unknown keys + supplies default).
    user_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingApproval:
        # Defensive: ignore unknown keys (forward-compat with future fields)
        # and supply defaults for missing optional fields.
        accepted = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in accepted}
        return cls(**clean)

    def is_active(self) -> bool:
        return self.status == "pending"

    def is_archivable(self, *, now: float | None = None) -> bool:
        if self.status == "pending":
            return False
        ref = self.resolved_at or self.created_at
        return (now or time.time()) - ref >= ARCHIVE_AFTER_SECONDS


# -----------------------------------------------------------------------------
# Hook type for SSE / IM event emission.
#
# Store calls ``hook(event_type, payload)`` after create / resolve. event_type
# ∈ {"pending_approval_created", "pending_approval_resolved"}. Hook must be
# fast + non-blocking + must NOT raise (Store wraps in try/except logger.warning).
# -----------------------------------------------------------------------------
EventHook = Callable[[str, dict[str, Any]], None]


@dataclass
class _StoreState:
    """Internal in-memory mirror of pending_approvals.json."""

    entries: dict[str, PendingApproval] = field(default_factory=dict)
    loaded: bool = False


class PendingApprovalsStore:
    """File-backed pending approvals store with archive rollover.

    Thread-safe across the single-process FastAPI server (RLock).
    Process-multi safety not provided — OpenAkita is single-process by design.
    """

    def __init__(
        self,
        *,
        data_dir: Path | str | None = None,
        event_hook: EventHook | None = None,
    ):
        if data_dir is None:
            from ..config import settings

            base = getattr(settings, "data_dir", None) or "data"
            data_dir = Path(base) / "scheduler"
        self._dir = Path(data_dir)
        self._main_path = self._dir / "pending_approvals.json"
        self._lock = threading.RLock()
        self._state = _StoreState()
        self._event_hook: EventHook | None = event_hook

    # ---- hook plumbing ----

    def set_event_hook(self, hook: EventHook | None) -> None:
        """Late-binding event hook (sse_bus might not exist when Store init)."""
        self._event_hook = hook

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        hook = self._event_hook
        if hook is None:
            return
        try:
            hook(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.warning(
                "[PendingApprovals] event_hook raised for %s; payload keys=%s",
                event_type,
                list(payload.keys()),
                exc_info=True,
            )

    # ---- load / persist ----

    def _ensure_loaded(self) -> None:
        if self._state.loaded:
            return
        with self._lock:
            if self._state.loaded:
                return
            self._dir.mkdir(parents=True, exist_ok=True)
            if not self._main_path.exists():
                self._state.loaded = True
                return
            try:
                raw = self._main_path.read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else {}
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "[PendingApprovals] failed to load %s: %s — starting empty",
                    self._main_path,
                    exc,
                )
                self._state.loaded = True
                return
            entries_raw = data.get("entries", [])
            if not isinstance(entries_raw, list):
                logger.warning(
                    "[PendingApprovals] %s: 'entries' is not a list (got %s); ignoring",
                    self._main_path,
                    type(entries_raw).__name__,
                )
                self._state.loaded = True
                return
            for raw_entry in entries_raw:
                if not isinstance(raw_entry, dict):
                    continue
                try:
                    entry = PendingApproval.from_dict(raw_entry)
                except (TypeError, ValueError) as exc:
                    logger.warning(
                        "[PendingApprovals] dropping malformed entry %r: %s",
                        raw_entry.get("id"),
                        exc,
                    )
                    continue
                self._state.entries[entry.id] = entry
            self._state.loaded = True
            logger.info(
                "[PendingApprovals] loaded %d entries from %s",
                len(self._state.entries),
                self._main_path,
            )

    def _persist(self) -> None:
        """Atomically write current state to disk + archive old entries."""
        # Caller must hold self._lock.
        self._dir.mkdir(parents=True, exist_ok=True)

        # Roll archivable entries out before serialization
        archivable = [e for e in self._state.entries.values() if e.is_archivable()]
        if archivable:
            self._archive_entries(archivable)
            for e in archivable:
                self._state.entries.pop(e.id, None)

        payload = {
            "version": 1,
            "saved_at": time.time(),
            "entries": [e.to_dict() for e in self._state.entries.values()],
        }
        tmp_path = self._main_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(str(tmp_path), str(self._main_path))
        except OSError as exc:
            logger.error(
                "[PendingApprovals] persist failed (%s); state may diverge from disk",
                exc,
            )
            # Best-effort cleanup of the temp file; ignore secondary failures.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _archive_entries(self, entries: Iterable[PendingApproval]) -> None:
        """Append entries to per-month jsonl. Lossy fail (warn + skip)."""
        # Caller holds lock.
        bucket: dict[str, list[PendingApproval]] = {}
        for e in entries:
            ref = e.resolved_at or e.created_at
            month = time.strftime("%Y%m", time.localtime(ref))
            bucket.setdefault(month, []).append(e)

        for month, items in bucket.items():
            archive_path = self._dir / f"pending_approvals_archive_{month}.jsonl"
            try:
                with archive_path.open("a", encoding="utf-8") as f:
                    for item in items:
                        f.write(json.dumps(item.to_dict(), ensure_ascii=False))
                        f.write("\n")
            except OSError as exc:
                logger.warning(
                    "[PendingApprovals] archive append failed (%s) for %s — "
                    "entries kept in main store",
                    exc,
                    archive_path,
                )

    # ---- public API ----

    def create(
        self,
        *,
        task_id: str | None,
        session_id: str,
        tool_name: str,
        params: dict[str, Any],
        approval_class: str | None,
        decision_chain: list[dict[str, Any]],
        decision_meta: dict[str, Any],
        reason: str,
        unattended_strategy: str,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        user_message: str = "",
    ) -> PendingApproval:
        """Create a new pending approval and persist it.

        Returns the freshly created ``PendingApproval`` (with assigned id).
        Emits ``pending_approval_created`` event after persist (so consumers
        only see committed state).
        """
        self._ensure_loaded()
        with self._lock:
            now = time.time()
            entry = PendingApproval(
                id=f"pa_{uuid.uuid4().hex[:12]}",
                task_id=task_id,
                session_id=session_id,
                tool_name=tool_name,
                params=dict(params),
                approval_class=approval_class,
                decision_chain=list(decision_chain),
                decision_meta=dict(decision_meta),
                reason=reason,
                unattended_strategy=unattended_strategy,
                created_at=now,
                expires_at=now + max(60.0, float(ttl_seconds)),
                user_message=user_message,
            )
            self._state.entries[entry.id] = entry
            self._persist()

        self._emit(
            "pending_approval_created",
            {
                "id": entry.id,
                "task_id": entry.task_id,
                "session_id": entry.session_id,
                "tool_name": entry.tool_name,
                "approval_class": entry.approval_class,
                "reason": entry.reason,
                "unattended_strategy": entry.unattended_strategy,
                "created_at": entry.created_at,
                "expires_at": entry.expires_at,
                "status": entry.status,
            },
        )
        return entry

    def list_active(self) -> list[PendingApproval]:
        """All entries with status=='pending' (excludes resolved/expired)."""
        self._ensure_loaded()
        with self._lock:
            now = time.time()
            # Lazy expire: any pending past expires_at gets bumped to EXPIRED
            # before listing so callers don't see stale entries.
            stale = [
                e
                for e in self._state.entries.values()
                if e.status == "pending" and e.expires_at <= now
            ]
            for e in stale:
                e.status = "expired"
                e.resolved_at = now
                e.resolution = "expired"
            if stale:
                self._persist()
                for e in stale:
                    self._emit(
                        "pending_approval_resolved",
                        {
                            "id": e.id,
                            "session_id": e.session_id,
                            "tool_name": e.tool_name,
                            "resolution": "expired",
                            "resolved_at": e.resolved_at,
                            "resolved_by": None,
                        },
                    )
            return [e for e in self._state.entries.values() if e.status == "pending"]

    def list_all(self) -> list[PendingApproval]:
        """All in-memory entries regardless of status (for admin views)."""
        self._ensure_loaded()
        with self._lock:
            return list(self._state.entries.values())

    def get(self, pending_id: str) -> PendingApproval | None:
        self._ensure_loaded()
        with self._lock:
            return self._state.entries.get(pending_id)

    def resolve(
        self,
        pending_id: str,
        *,
        decision: str,
        resolved_by: str | None,
        note: str = "",
    ) -> PendingApproval | None:
        """Resolve a pending approval.

        ``decision`` ∈ {"allow", "deny"}. Returns the updated entry or None
        if pending_id unknown. Idempotent: resolving an already-resolved
        entry returns it unchanged + emits no event.

        Caller is responsible for any post-resolve action (re-running the
        scheduled task with ReplayAuthorization injected — see
        scheduler/executor.py).
        """
        if decision not in ("allow", "deny"):
            raise ValueError(f"decision must be 'allow' or 'deny', got {decision!r}")
        self._ensure_loaded()
        with self._lock:
            entry = self._state.entries.get(pending_id)
            if entry is None:
                return None
            if entry.status != "pending":
                # Already resolved — idempotent; no event re-fire.
                return entry
            entry.status = "approved" if decision == "allow" else "denied"
            entry.resolution = decision
            entry.resolved_at = time.time()
            entry.resolved_by = resolved_by
            entry.note = note
            self._persist()
        self._emit(
            "pending_approval_resolved",
            {
                "id": entry.id,
                "session_id": entry.session_id,
                "tool_name": entry.tool_name,
                "resolution": entry.resolution,
                "resolved_at": entry.resolved_at,
                "resolved_by": entry.resolved_by,
                "note": entry.note,
            },
        )
        return entry

    def stats(self) -> dict[str, int]:
        """Quick counts by status — for /api/pending_approvals/stats."""
        self._ensure_loaded()
        with self._lock:
            counts = {"pending": 0, "approved": 0, "denied": 0, "expired": 0}
            for e in self._state.entries.values():
                counts[e.status] = counts.get(e.status, 0) + 1
            return counts


# -----------------------------------------------------------------------------
# Module-level singleton (lazy)
# -----------------------------------------------------------------------------

_singleton: PendingApprovalsStore | None = None
_singleton_lock = threading.Lock()


def get_pending_approvals_store() -> PendingApprovalsStore:
    """Lazy singleton. First caller decides data_dir via settings."""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PendingApprovalsStore()
    return _singleton


def reset_pending_approvals_store() -> None:
    """Test helper. Drops the singleton + in-memory state (NOT the disk file)."""
    global _singleton
    with _singleton_lock:
        _singleton = None
