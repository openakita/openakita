"""C17 Phase A — scheduler 单任务执行锁 + 崩溃恢复 + task_id 链路单测。

覆盖：

- ``acquire_exec_lock`` 的 O_EXCL 独占语义、stale lock 自动回收、PID 复用窗口防护
- ``heartbeat_exec_lock`` 心跳与 lease 推进、跨进程接管检测
- ``scan_orphaned_locks`` startup rescan 探测各种 stale 原因
- ``set_current_scheduled_task_id`` ContextVar 在 ``tool_executor``
  ``_defer_unattended_confirm`` 兜底 path 的可用性（不在内置 state 时）
- ``TaskScheduler._stagger_missed_tasks`` 把多余 missed 任务推迟，避免雷群

测试不依赖 IM / Gateway / LLM——纯本地文件 + ContextVar。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openakita.scheduler import locks
from openakita.scheduler.locks import (
    acquire_exec_lock,
    default_lock_dir,
    get_current_scheduled_task_id,
    heartbeat_exec_lock,
    is_stale,
    release_exec_lock,
    reset_current_scheduled_task_id,
    scan_orphaned_locks,
    set_current_scheduled_task_id,
    unlink_orphan,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lock_dir(tmp_path: Path) -> Path:
    d = tmp_path / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch):
    """We never want a unit test to actually sleep for retry windows."""
    monkeypatch.setattr(time, "sleep", lambda *_a, **_kw: None)


# ---------------------------------------------------------------------------
# acquire_exec_lock
# ---------------------------------------------------------------------------


class TestAcquireExecLock:
    def test_o_excl_first_attempt_succeeds(self, lock_dir: Path) -> None:
        lock = acquire_exec_lock(
            "task_alpha", lock_dir=lock_dir, expected_runtime_s=60
        )
        assert lock is not None
        assert lock.task_id == "task_alpha"
        assert lock.pid == os.getpid()
        assert lock.lock_path.exists()
        on_disk = json.loads(lock.lock_path.read_text("utf-8"))
        assert on_disk["task_id"] == "task_alpha"
        assert on_disk["execution_id"] == lock.execution_id
        # lease_until ≥ acquired_at + MIN_LEASE_SECONDS (since runtime 60 → 120)
        acquired = datetime.fromisoformat(on_disk["acquired_at"])
        lease = datetime.fromisoformat(on_disk["lease_until"])
        assert (lease - acquired).total_seconds() >= locks.MIN_LEASE_SECONDS - 1

    def test_second_acquire_when_first_live_returns_none(
        self, lock_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = acquire_exec_lock("task_beta", lock_dir=lock_dir)
        assert first is not None
        # Make PID liveness always return True — first lock is "alive".
        monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
        second = acquire_exec_lock("task_beta", lock_dir=lock_dir)
        assert second is None
        # Released to keep tmp clean
        release_exec_lock(first)

    def test_stale_pid_dead_lock_reacquired(
        self, lock_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = acquire_exec_lock("task_gamma", lock_dir=lock_dir)
        assert first is not None
        # Simulate the prior holder's PID being dead.
        monkeypatch.setattr(locks, "_pid_alive", lambda pid: False)
        second = acquire_exec_lock("task_gamma", lock_dir=lock_dir)
        assert second is not None
        assert second.execution_id != first.execution_id
        release_exec_lock(second)

    def test_stale_lease_expired_lock_reacquired(
        self, lock_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = acquire_exec_lock("task_delta", lock_dir=lock_dir)
        assert first is not None
        # Force lease into the past.
        data = json.loads(first.lock_path.read_text("utf-8"))
        past = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
        data["lease_until"] = past
        first.lock_path.write_text(json.dumps(data), encoding="utf-8")
        # Even if PID alive, lease being in the past wins.
        monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
        second = acquire_exec_lock("task_delta", lock_dir=lock_dir)
        assert second is not None
        release_exec_lock(second)

    def test_release_is_idempotent(self, lock_dir: Path) -> None:
        lock = acquire_exec_lock("task_eps", lock_dir=lock_dir)
        assert lock is not None
        release_exec_lock(lock)
        # Second release must not raise even though the file is already gone.
        release_exec_lock(lock)
        # And a None lock is fine.
        release_exec_lock(None)


# ---------------------------------------------------------------------------
# heartbeat_exec_lock
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_heartbeat_pushes_lease_forward(self, lock_dir: Path) -> None:
        lock = acquire_exec_lock(
            "task_hb", lock_dir=lock_dir, expected_runtime_s=120
        )
        assert lock is not None
        original_lease = datetime.fromisoformat(lock.lease_until)
        time.sleep_called = 0  # noqa: SLF001  (autouse fixture stubs sleep)
        ok = heartbeat_exec_lock(lock, expected_runtime_s=240)
        assert ok is True
        # heartbeat_at updated; lease pushed forward.
        new_lease = datetime.fromisoformat(lock.lease_until)
        assert new_lease >= original_lease

    def test_heartbeat_returns_false_when_taken_over(
        self, lock_dir: Path
    ) -> None:
        lock = acquire_exec_lock("task_takeover", lock_dir=lock_dir)
        assert lock is not None
        # Overwrite the on-disk record with a different execution_id —
        # simulating another process grabbing the lease after stale-recovery.
        data = json.loads(lock.lock_path.read_text("utf-8"))
        data["execution_id"] = "exec_someone_else"
        lock.lock_path.write_text(json.dumps(data), encoding="utf-8")
        ok = heartbeat_exec_lock(lock)
        assert ok is False

    def test_heartbeat_on_missing_file_returns_false(
        self, lock_dir: Path
    ) -> None:
        lock = acquire_exec_lock("task_missing", lock_dir=lock_dir)
        assert lock is not None
        release_exec_lock(lock)
        ok = heartbeat_exec_lock(lock)
        assert ok is False


# ---------------------------------------------------------------------------
# is_stale + scan_orphaned_locks
# ---------------------------------------------------------------------------


class TestIsStale:
    def test_malformed_record_is_stale(self) -> None:
        stale, reason = is_stale({"foo": "bar"})
        assert stale is True
        assert reason == "malformed"

    def test_lease_expired_is_stale(self) -> None:
        now = datetime.now(UTC)
        data = {
            "pid": os.getpid(),
            "lease_until": (now - timedelta(seconds=10)).isoformat(),
            "heartbeat_at": now.isoformat(),
        }
        stale, reason = is_stale(data, now=now)
        assert stale is True
        assert reason == "lease_expired"

    def test_pid_dead_is_stale(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = datetime.now(UTC)
        data = {
            "pid": 999999,  # unlikely to exist
            "lease_until": (now + timedelta(seconds=300)).isoformat(),
            "heartbeat_at": now.isoformat(),
        }
        monkeypatch.setattr(locks, "_pid_alive", lambda pid: False)
        stale, reason = is_stale(data, now=now)
        assert stale is True
        assert reason == "pid_dead"

    def test_heartbeat_stalled_is_stale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = datetime.now(UTC)
        very_old = now - timedelta(
            seconds=locks.HEARTBEAT_INTERVAL_SECONDS
            * locks.HEARTBEAT_STALE_FACTOR
            * 2
        )
        data = {
            "pid": os.getpid(),
            "lease_until": (now + timedelta(seconds=600)).isoformat(),
            "heartbeat_at": very_old.isoformat(),
        }
        monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
        stale, reason = is_stale(data, now=now)
        assert stale is True
        assert reason == "heartbeat_stalled"

    def test_live_lock_not_stale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = datetime.now(UTC)
        data = {
            "pid": os.getpid(),
            "lease_until": (now + timedelta(seconds=300)).isoformat(),
            "heartbeat_at": now.isoformat(),
        }
        monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
        stale, reason = is_stale(data, now=now)
        assert stale is False
        assert reason == "live"


class TestScanOrphanedLocks:
    def test_empty_dir_returns_empty(self, lock_dir: Path) -> None:
        assert scan_orphaned_locks(lock_dir) == []

    def test_live_lock_not_in_orphans(
        self, lock_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lock = acquire_exec_lock("task_live", lock_dir=lock_dir)
        assert lock is not None
        monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
        orphans = scan_orphaned_locks(lock_dir)
        assert orphans == []
        release_exec_lock(lock)

    def test_stale_lock_appears_in_orphans(
        self, lock_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lock = acquire_exec_lock("task_stale", lock_dir=lock_dir)
        assert lock is not None
        monkeypatch.setattr(locks, "_pid_alive", lambda pid: False)
        orphans = scan_orphaned_locks(lock_dir)
        assert len(orphans) == 1
        assert orphans[0].task_id == "task_stale"
        assert orphans[0].reason == "pid_dead"
        unlink_orphan(orphans[0])
        assert not lock.lock_path.exists()

    def test_unreadable_lock_is_orphan(self, lock_dir: Path) -> None:
        bad = lock_dir / "exec_garbage.json"
        bad.write_text("not-json-at-all", encoding="utf-8")
        orphans = scan_orphaned_locks(lock_dir)
        assert len(orphans) == 1
        assert orphans[0].reason == "unreadable"
        assert orphans[0].task_id == "garbage"


# ---------------------------------------------------------------------------
# ContextVar bridge (Phase A.4)
# ---------------------------------------------------------------------------


class TestScheduledTaskIdContextVar:
    def test_default_is_none(self) -> None:
        assert get_current_scheduled_task_id() is None

    def test_set_and_reset_round_trip(self) -> None:
        token = set_current_scheduled_task_id("task_xyz")
        try:
            assert get_current_scheduled_task_id() == "task_xyz"
        finally:
            reset_current_scheduled_task_id(token)
        assert get_current_scheduled_task_id() is None

    def test_isolation_across_asyncio_tasks(self) -> None:
        """asyncio.Task captures the surrounding ContextVar at creation time.

        Setting in the parent → child task sees the value; setting in
        child → parent doesn't.
        """

        async def child_reads() -> str | None:
            return get_current_scheduled_task_id()

        async def parent_sets_child_reads() -> tuple[str | None, str | None]:
            token = set_current_scheduled_task_id("task_parent")
            try:
                inner = await asyncio.create_task(child_reads())
            finally:
                reset_current_scheduled_task_id(token)
            return inner, get_current_scheduled_task_id()

        inner_seen, parent_after = asyncio.run(parent_sets_child_reads())
        assert inner_seen == "task_parent"
        assert parent_after is None


# ---------------------------------------------------------------------------
# TaskScheduler integration: startup rescan + stagger + execute_task
# ---------------------------------------------------------------------------


@pytest.fixture
def scheduler_storage(tmp_path: Path) -> Path:
    d = tmp_path / "scheduler"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestTaskSchedulerStartupRecovery:
    def _make_task(self, tid: str = "task_int_1"):
        from openakita.scheduler.task import ScheduledTask, TriggerType

        return ScheduledTask(
            id=tid,
            name=f"name_{tid}",
            description="",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"interval_minutes": 5},
            prompt="echo hi",
        )

    def test_default_lock_dir_layout(self, scheduler_storage: Path) -> None:
        d = default_lock_dir(scheduler_storage)
        assert d == scheduler_storage / "locks"

    def test_start_clears_orphan_lock_and_resets_running(
        self, scheduler_storage: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openakita.scheduler.scheduler import TaskScheduler
        from openakita.scheduler.task import TaskStatus

        scheduler = TaskScheduler(
            storage_path=scheduler_storage,
            executor=None,
            check_interval_seconds=1,
        )
        task = self._make_task("task_recover")
        # Hand-craft RUNNING state + leftover lock file (simulating crash).
        task.status = TaskStatus.RUNNING
        scheduler._tasks[task.id] = task

        # Make sure orphan lock looks stale (PID 1 likely dead in unit envs).
        # We explicitly force is_stale to True via monkeypatch.
        lock = acquire_exec_lock(task.id, lock_dir=scheduler.lock_dir)
        assert lock is not None
        monkeypatch.setattr(locks, "_pid_alive", lambda pid: False)

        orphans = scheduler._rescan_orphaned_runs()
        assert len(orphans) == 1
        assert orphans[0].task_id == task.id
        # Task got force-reset back to SCHEDULED.
        assert scheduler._tasks[task.id].status == TaskStatus.SCHEDULED
        # Lock file is gone, recovery.jsonl recorded the event.
        assert not lock.lock_path.exists()
        recovery_log = scheduler_storage / "recovery.jsonl"
        assert recovery_log.exists()
        line = recovery_log.read_text("utf-8").strip().splitlines()[-1]
        record = json.loads(line)
        assert record["kind"] == "orphan_lock"
        assert record["task_id"] == task.id
        assert record["reason"] == "pid_dead"

    def test_stagger_missed_tasks_caps_at_max(
        self, scheduler_storage: Path
    ) -> None:
        from openakita.scheduler.scheduler import TaskScheduler

        scheduler = TaskScheduler(
            storage_path=scheduler_storage,
            executor=None,
            check_interval_seconds=1,
        )
        # MAX_MISSED_PER_RESTART defaults to 10; create 13 tasks.
        now = datetime.now()
        tasks = []
        for i in range(13):
            t = self._make_task(f"task_miss_{i}")
            t.next_run = now - timedelta(hours=1)
            tasks.append(t)
            scheduler._tasks[t.id] = t
        scheduler._stagger_missed_tasks(tasks, now)
        # The first MAX tasks keep their original (past) next_run.
        for i in range(scheduler.MAX_MISSED_PER_RESTART):
            assert tasks[i].next_run < now
        # Beyond cap, next_run is pushed into the future, monotonic by index.
        prev = now
        for i in range(scheduler.MAX_MISSED_PER_RESTART, len(tasks)):
            assert tasks[i].next_run > prev
            prev = tasks[i].next_run

    @pytest.mark.asyncio
    async def test_execute_task_persists_running_then_releases_lock(
        self, scheduler_storage: Path
    ) -> None:
        """Black-box: a no-op executor → status persisted RUNNING, then
        execution completes and exec lock file is gone, ContextVar is reset."""
        from openakita.scheduler.scheduler import TaskScheduler

        captured: dict[str, str | None] = {"task_id_in_exec": None}

        async def _executor(task) -> tuple[bool, str]:
            captured["task_id_in_exec"] = get_current_scheduled_task_id()
            return True, "ok"

        scheduler = TaskScheduler(
            storage_path=scheduler_storage,
            executor=_executor,
            check_interval_seconds=1,
        )
        task = self._make_task("task_run_round_trip")
        await scheduler.add_task(task)
        await scheduler.start()
        try:
            exec_record = await scheduler.trigger_now(task.id)
        finally:
            await scheduler.stop(graceful_timeout=2.0)

        assert exec_record is not None
        assert exec_record.status == "success"
        # ContextVar was visible to executor.
        assert captured["task_id_in_exec"] == task.id
        # ContextVar reset after run.
        assert get_current_scheduled_task_id() is None
        # No lingering lock file.
        lock_file = scheduler.lock_dir / f"exec_{task.id}.json"
        assert not lock_file.exists(), (
            f"lock file should be released, but {lock_file} still exists"
        )
