"""C17 Phase A — Scheduler 单任务执行锁（O_EXCL + PID + heartbeat + lease）。

设计目标
========

OpenAkita 之前的调度器只在内存里维护 ``_running_tasks: set[str]``。一旦
后端被 ``SIGKILL`` / OOM / Windows Task Manager "End Task" 中止，下次
重启时 ``tasks.json`` 里 status=running 的任务无人收尸——可能造成：

1. 任务被双跑（恢复后 ``_scheduler_loop`` 看到 next_run 落在过去，立刻再
   触发；同时另一个慢启动的崩溃进程才恢复完成并继续写记录）。
2. 静默漏跑（status 卡在 ``running`` 没人推进 next_run）。

C17 引入**进程级执行租约**：每次开始执行一个 ``ScheduledTask`` 前，先在
``data/scheduler/locks/`` 写一个独占的 ``exec_<task_id>.json`` 锁文件，
``O_EXCL | O_CREAT | O_WRONLY`` 打开——存在 → 检查 PID 是否还活着 + lease
是否过期 → stale 则 unlink 重抢，否则放弃本轮。

借鉴的业界做法（与 plan ``c17_reliability_09fbe9fc.plan.md`` Phase A 对齐）：

- claude-code ``cronTasksLock.ts``: ``O_EXCL`` 抢锁 + 写入 PID + 启动时探活。
  我们沿用 PID 探活 + lease 双判定（防 PID 复用窗口）。
- hermes-agent ``cron/jobs.py``: 执行前先 ``advance_next_run`` 推进
  next_run。本模块只负责 lock，next_run 推进由 scheduler.py Phase A.2 完成。
- openclaw ``planStartupCatchup``: missed 任务 stagger + cap，由 Phase A.3
  在 ``TaskScheduler.start()`` 里调用 :func:`scan_orphaned_locks`。

锁文件结构
==========

::

    {
      "task_id": "task_abc123",
      "execution_id": "exec_<uuid>",
      "pid": 12345,
      "hostname": "...",
      "acquired_at": "2026-05-14T16:30:00+08:00",
      "heartbeat_at": "2026-05-14T16:30:30+08:00",
      "lease_until": "2026-05-14T16:35:00+08:00"
    }

- ``heartbeat_at`` 由 :func:`heartbeat_exec_lock` 每 ~60s 更新一次，证明
  持有者还在工作；``lease_until`` 是更宽的硬截止（默认 max(120s,
  expected_runtime * 2)），即使心跳停了也容忍一段时间。
- :func:`is_stale` 同时校验 PID 是否还活着（``os.kill(pid, 0)``）和
  ``lease_until`` 是否过期——任一不满足 → stale。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables — kept as module-level constants so tests can monkeypatch.
# ---------------------------------------------------------------------------

# Minimum lease duration: 2 minutes. Caller-supplied expected_runtime_s 会被
# 放大到 2x 但下限不低于这里（短任务也要给至少 2 分钟，给慢启动 / GC 留余地）。
MIN_LEASE_SECONDS: int = 120

# 心跳间隔。Heartbeat task loop 会按这个节奏调 :func:`heartbeat_exec_lock`。
HEARTBEAT_INTERVAL_SECONDS: int = 60

# 心跳超过这个倍数没刷新 → 配合 lease 判 stale（保险）。
HEARTBEAT_STALE_FACTOR: int = 4


@dataclass
class ExecLock:
    """Active exec lock holder. Hand back to :func:`release_exec_lock`."""

    task_id: str
    execution_id: str
    pid: int
    hostname: str
    acquired_at: str
    heartbeat_at: str
    lease_until: str
    lock_path: Path = field(default_factory=lambda: Path())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["lock_path"] = str(self.lock_path)
        return d


@dataclass(frozen=True)
class OrphanLock:
    """Output row of :func:`scan_orphaned_locks` — a lock file deemed stale."""

    task_id: str
    lock_path: Path
    reason: str
    pid: int | None
    hostname: str | None
    acquired_at: str | None
    heartbeat_at: str | None
    lease_until: str | None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def default_lock_dir(scheduler_storage_path: Path | str) -> Path:
    """Return the directory where exec locks live for a scheduler storage path.

    Mirrors the layout used by ``data/scheduler/`` so audit / GC tools can
    discover locks without knowing about the writer.
    """
    base = Path(scheduler_storage_path)
    return base / "locks"


def _lock_path_for(lock_dir: Path, task_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in task_id)
    return lock_dir / f"exec_{safe}.json"


# ---------------------------------------------------------------------------
# PID liveness — small wrapper so tests can fake it on Windows.
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    """Best-effort PID liveness check.

    On POSIX, ``os.kill(pid, 0)`` raises ``OSError`` if the PID doesn't exist
    or if the caller has no permission. We treat ``PermissionError`` as
    "alive" (some other user owns it, but it does exist — better to assume
    alive and let lease expiry break the tie).

    On Windows, ``os.kill(pid, 0)`` is implemented via ``OpenProcess`` and
    raises ``OSError`` for missing PIDs, which is exactly what we want.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False


# ---------------------------------------------------------------------------
# Stale detection
# ---------------------------------------------------------------------------


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _now_utc() -> datetime:
    return datetime.now(UTC)


def is_stale(data: dict[str, Any], *, now: datetime | None = None) -> tuple[bool, str]:
    """Return ``(stale, reason)`` for an on-disk lock record.

    Order of checks:

    1. Malformed / missing fields → stale ("malformed").
    2. ``lease_until`` < now → stale ("lease_expired").
    3. ``pid`` not alive → stale ("pid_dead").
    4. ``heartbeat_at`` older than HEARTBEAT_INTERVAL * HEARTBEAT_STALE_FACTOR
       → stale ("heartbeat_stalled").

    Otherwise return ``(False, "live")``.
    """
    now = now or _now_utc()

    pid = data.get("pid")
    lease = _parse_iso(data.get("lease_until"))
    heartbeat = _parse_iso(data.get("heartbeat_at"))
    if not isinstance(pid, int) or lease is None or heartbeat is None:
        return True, "malformed"

    if lease < now:
        return True, "lease_expired"

    if not _pid_alive(pid):
        return True, "pid_dead"

    hb_deadline = heartbeat + timedelta(
        seconds=HEARTBEAT_INTERVAL_SECONDS * HEARTBEAT_STALE_FACTOR
    )
    if hb_deadline < now:
        return True, "heartbeat_stalled"

    return False, "live"


# ---------------------------------------------------------------------------
# Acquire / release
# ---------------------------------------------------------------------------


def _serialize_record(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def acquire_exec_lock(
    task_id: str,
    *,
    lock_dir: Path,
    expected_runtime_s: float | int | None = None,
    execution_id: str | None = None,
    pid: int | None = None,
) -> ExecLock | None:
    """Try to acquire an exclusive exec lock for ``task_id``.

    Returns the populated :class:`ExecLock` on success, or ``None`` if
    another live process holds the lease.

    Stale locks (PID dead / lease expired / heartbeat stalled) are
    unlinked and a fresh attempt is made (one retry — avoids races where
    two recoverers fight over the same dead lock).
    """
    if not task_id:
        raise ValueError("task_id must be non-empty")
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path_for(lock_dir, task_id)

    pid = pid if pid is not None else os.getpid()
    runtime = float(expected_runtime_s) if expected_runtime_s else 0.0
    lease_seconds = max(MIN_LEASE_SECONDS, int(runtime * 2)) if runtime > 0 else MIN_LEASE_SECONDS

    for attempt in range(2):
        now = _now_utc()
        record = {
            "task_id": task_id,
            "execution_id": execution_id or f"exec_{uuid.uuid4().hex[:12]}",
            "pid": pid,
            "hostname": socket.gethostname(),
            "acquired_at": now.isoformat(),
            "heartbeat_at": now.isoformat(),
            "lease_until": (now + timedelta(seconds=lease_seconds)).isoformat(),
        }
        try:
            fd = os.open(
                str(lock_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            existing = _read_lock_safely(lock_path)
            if existing is None:
                # Treat unreadable file as stale on retry only — first
                # attempt leaves it alone (might still be flushing).
                if attempt == 0:
                    time.sleep(0.05)
                    continue
                _unlink_stale(lock_path, reason="unreadable")
                continue
            stale, reason = is_stale(existing)
            if not stale:
                logger.info(
                    "[scheduler.locks] %s held by pid=%s lease_until=%s; "
                    "skipping acquire (reason=live)",
                    task_id,
                    existing.get("pid"),
                    existing.get("lease_until"),
                )
                return None
            _unlink_stale(lock_path, reason=reason, prior=existing)
            continue
        except OSError as exc:
            logger.error(
                "[scheduler.locks] acquire(%s) OSError opening %s: %s",
                task_id,
                lock_path,
                exc,
            )
            return None

        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(_serialize_record(record))
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # fsync is best-effort; we'll still drop the file even
                    # if the OS doesn't honour it.
                    pass
        except OSError as exc:
            logger.error(
                "[scheduler.locks] acquire(%s) write failed, unlinking: %s",
                task_id,
                exc,
            )
            _unlink_stale(lock_path, reason="write_failed")
            return None

        return ExecLock(
            task_id=task_id,
            execution_id=record["execution_id"],
            pid=record["pid"],
            hostname=record["hostname"],
            acquired_at=record["acquired_at"],
            heartbeat_at=record["heartbeat_at"],
            lease_until=record["lease_until"],
            lock_path=lock_path,
        )

    return None


def release_exec_lock(lock: ExecLock | None) -> None:
    """Best-effort delete of the lock file. Safe to call twice."""
    if lock is None:
        return
    try:
        if lock.lock_path.exists():
            lock.lock_path.unlink()
    except OSError as exc:
        logger.warning(
            "[scheduler.locks] release(%s) unlink failed: %s",
            lock.task_id,
            exc,
        )


def heartbeat_exec_lock(
    lock: ExecLock,
    *,
    expected_runtime_s: float | int | None = None,
) -> bool:
    """Refresh ``heartbeat_at`` (and extend ``lease_until``) on disk.

    Returns ``True`` if the on-disk record still matches our
    ``execution_id`` (we still own the lock); ``False`` if someone else
    took over (stale-recovered + reissued).
    """
    if not lock.lock_path.exists():
        return False

    existing = _read_lock_safely(lock.lock_path)
    if not existing or existing.get("execution_id") != lock.execution_id:
        return False

    now = _now_utc()
    runtime = float(expected_runtime_s) if expected_runtime_s else 0.0
    lease_seconds = max(MIN_LEASE_SECONDS, int(runtime * 2)) if runtime > 0 else MIN_LEASE_SECONDS

    record = dict(existing)
    record["heartbeat_at"] = now.isoformat()
    record["lease_until"] = (now + timedelta(seconds=lease_seconds)).isoformat()
    # Track PID changes (e.g. Tauri spawned child) — keep current PID so
    # `is_stale` checks the right process.
    record["pid"] = os.getpid()

    try:
        # Write+rename for atomic update on POSIX/NTFS.
        tmp = lock.lock_path.with_suffix(lock.lock_path.suffix + ".tmp")
        tmp.write_bytes(_serialize_record(record))
        os.replace(str(tmp), str(lock.lock_path))
    except OSError as exc:
        logger.warning(
            "[scheduler.locks] heartbeat(%s) write failed: %s",
            lock.task_id,
            exc,
        )
        return False

    lock.heartbeat_at = record["heartbeat_at"]
    lock.lease_until = record["lease_until"]
    lock.pid = record["pid"]
    return True


# ---------------------------------------------------------------------------
# Diagnostics + startup rescan
# ---------------------------------------------------------------------------


def _read_lock_safely(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _unlink_stale(
    path: Path, *, reason: str, prior: dict[str, Any] | None = None
) -> None:
    try:
        path.unlink()
        logger.warning(
            "[scheduler.locks] unlinked stale lock %s (reason=%s, prior=%s)",
            path.name,
            reason,
            prior if prior is not None else "<unreadable>",
        )
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.error(
            "[scheduler.locks] failed to unlink stale lock %s: %s",
            path,
            exc,
        )


def scan_orphaned_locks(lock_dir: Path) -> list[OrphanLock]:
    """Return all lock files that are stale (caller will unlink + audit).

    Used on startup by :class:`TaskScheduler` to reconcile the in-memory
    task list with leftover locks from crashed runs.
    """
    if not lock_dir.exists():
        return []
    out: list[OrphanLock] = []
    for path in sorted(lock_dir.glob("exec_*.json")):
        data = _read_lock_safely(path)
        if data is None:
            out.append(
                OrphanLock(
                    task_id=path.stem[len("exec_") :],
                    lock_path=path,
                    reason="unreadable",
                    pid=None,
                    hostname=None,
                    acquired_at=None,
                    heartbeat_at=None,
                    lease_until=None,
                )
            )
            continue
        stale, reason = is_stale(data)
        if not stale:
            continue
        out.append(
            OrphanLock(
                task_id=str(data.get("task_id") or path.stem[len("exec_") :]),
                lock_path=path,
                reason=reason,
                pid=data.get("pid") if isinstance(data.get("pid"), int) else None,
                hostname=data.get("hostname") if isinstance(data.get("hostname"), str) else None,
                acquired_at=data.get("acquired_at") if isinstance(data.get("acquired_at"), str) else None,
                heartbeat_at=data.get("heartbeat_at") if isinstance(data.get("heartbeat_at"), str) else None,
                lease_until=data.get("lease_until") if isinstance(data.get("lease_until"), str) else None,
            )
        )
    return out


def unlink_orphan(orphan: OrphanLock) -> None:
    """Delete an orphan lock file flagged by :func:`scan_orphaned_locks`."""
    _unlink_stale(orphan.lock_path, reason=f"orphan:{orphan.reason}")


# ---------------------------------------------------------------------------
# Scheduled-task ContextVar (Phase A.4)
#
# Used to thread the currently-executing scheduled task id through deep
# call chains where ``state.task_id`` is missing — specifically
# ``tool_executor._defer_unattended_confirm`` needs the task id when the
# scheduler invokes a sub-agent that has no ``begin_task`` registration.
# ---------------------------------------------------------------------------

import contextvars  # noqa: E402

_current_scheduled_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "openakita_current_scheduled_task_id", default=None
)


def set_current_scheduled_task_id(task_id: str | None):
    """Push ``task_id`` onto the ContextVar; returns a token for reset.

    The returned token must be passed back to
    :func:`reset_current_scheduled_task_id`. Mirrors the C15
    ``evolution_fix_id`` pattern.
    """
    return _current_scheduled_task_id.set(task_id)


def reset_current_scheduled_task_id(token: contextvars.Token) -> None:
    """Pop the most recent :func:`set_current_scheduled_task_id` value."""
    try:
        _current_scheduled_task_id.reset(token)
    except (ValueError, LookupError):
        # Token from a different context — best-effort; nothing else to do.
        pass


def get_current_scheduled_task_id() -> str | None:
    """Return the active scheduled task id, or ``None`` outside a scheduler run."""
    return _current_scheduled_task_id.get()


# ---------------------------------------------------------------------------
# Heartbeat helpers — convenience for callers that want to run a periodic
# refresh from an asyncio task. Kept sync-friendly so the scheduler module
# can opt in without forcing this module to import asyncio at top level.
# ---------------------------------------------------------------------------


def _next_heartbeat_due(lock: ExecLock) -> float:
    """Wall-clock seconds until the next heartbeat is due (always > 0)."""
    last = _parse_iso(lock.heartbeat_at) or _now_utc()
    due = (last + timedelta(seconds=HEARTBEAT_INTERVAL_SECONDS) - _now_utc()).total_seconds()
    return max(1.0, due)


_HB_THREADS: dict[str, threading.Event] = {}


def start_background_heartbeat(
    lock: ExecLock,
    *,
    expected_runtime_s: float | int | None = None,
) -> threading.Event:
    """Spin a daemon thread to refresh the lock heartbeat in the background.

    Returns a ``threading.Event`` — call ``.set()`` to stop the thread.
    Use this when the caller doesn't have a convenient ``asyncio`` loop
    (e.g. sync test harnesses); production scheduler uses an
    ``asyncio.Task`` directly.
    """
    stop = threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            wait_s = _next_heartbeat_due(lock)
            if stop.wait(wait_s):
                return
            try:
                heartbeat_exec_lock(lock, expected_runtime_s=expected_runtime_s)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[scheduler.locks] background heartbeat(%s) raised %s",
                    lock.task_id,
                    exc,
                )

    t = threading.Thread(target=_loop, name=f"exec-lock-hb-{lock.task_id}", daemon=True)
    t.start()
    _HB_THREADS[lock.execution_id] = stop
    return stop


__all__ = [
    "ExecLock",
    "HEARTBEAT_INTERVAL_SECONDS",
    "HEARTBEAT_STALE_FACTOR",
    "MIN_LEASE_SECONDS",
    "OrphanLock",
    "acquire_exec_lock",
    "default_lock_dir",
    "get_current_scheduled_task_id",
    "heartbeat_exec_lock",
    "is_stale",
    "release_exec_lock",
    "reset_current_scheduled_task_id",
    "scan_orphaned_locks",
    "set_current_scheduled_task_id",
    "start_background_heartbeat",
    "unlink_orphan",
]
