"""Contract suite for v2 OrgNodeScheduler (P-RC-9 P9.3d).

Twelve cases pin the public surface and internal invariants of
``openakita.orgs.node_scheduler.OrgNodeScheduler``
against a single in-memory backend (NodeScheduler has no
JSON/SQLite split -- persistence is delegated to the injected
:class:`ScheduleStore` Protocol).

The cases are grouped by the P9.3 charter axes:

1. ``compute_next_fire_time`` pure helper (3 cases: INTERVAL,
   ONCE, CRON fall-through).
2. Lifecycle (4 cases: start with no schedules, start with
   one, multi-node multi-schedule, disabled schedule
   skipped).
3. Cancel / reload semantics (2 cases:
   ``stop_for_org`` cancels owned tasks; ``reload`` swaps the
   task without losing the schedule).
4. Concurrency / Nit-2 stress (1 case: 4 workers x 25 reload
   cycles = 100 concurrent operations).
5. Dispatch invariants (2 cases: ``trigger_once`` invokes
   dispatcher with v1-faithful prompt + records lifecycle
   events; missing schedule id returns
   ``{\"error\": ...}``).

Same pattern as ``tests/runtime/orgs/test_blackboard_contract.py``
(P9.1d) + ``test_project_store_contract.py`` (P9.2e/e2). The
12 cases are independent (no shared fixtures across tests) so
flakes do not cascade.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from openakita.orgs.node_scheduler import (
    OrgNodeScheduler,
    build_schedule_prompt,
    compute_next_fire_time,
)
from openakita.orgs.scheduler_models import (
    NodeSchedule,
    ScheduleType,
    new_schedule_id,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class RecordingDispatcher:
    """In-memory :class:`CommandDispatcher` capturing every call."""

    def __init__(self, result: dict | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._result = result if result is not None else {"result": "ok"}

    async def dispatch(self, org_id: str, node_id: str, prompt: str) -> dict:
        self.calls.append((org_id, node_id, prompt))
        return dict(self._result)


class InMemoryStore:
    """In-memory :class:`ScheduleStore` -- the single backend P9.3 ships."""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], list[NodeSchedule]] = {}

    def get_node_schedules(self, org_id: str, node_id: str) -> list[NodeSchedule]:
        return list(self._d.get((org_id, node_id), []))

    def save_node_schedules(self, org_id: str, node_id: str, schedules: list[NodeSchedule]) -> None:
        self._d[(org_id, node_id)] = list(schedules)


class RecordingProbe:
    """In-memory :class:`SchedulerRuntimeProbe` capturing every event."""

    def __init__(self, runnable: bool = True) -> None:
        self.events: list[tuple[str, str, dict]] = []
        self.runnable = runnable

    def is_node_runnable(self, org_id: str, node_id: str) -> bool:
        return self.runnable

    def emit_event(self, org_id: str, event_type: str, node_id: str, payload: dict) -> None:
        self.events.append((event_type, node_id, dict(payload)))


def _make_scheduler() -> tuple[
    OrgNodeScheduler, RecordingDispatcher, InMemoryStore, RecordingProbe
]:
    d = RecordingDispatcher()
    s = InMemoryStore()
    p = RecordingProbe()
    return OrgNodeScheduler(d, s, p), d, s, p


# ---------------------------------------------------------------------------
# 1-3. compute_next_fire_time pure helper
# ---------------------------------------------------------------------------


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


def test_compute_next_fire_interval() -> None:
    sched = NodeSchedule(name="i", schedule_type=ScheduleType.INTERVAL, interval_s=900)
    nxt = compute_next_fire_time(sched, _NOW)
    assert (nxt - _NOW).total_seconds() == 900


def test_compute_next_fire_once_utc_coerced() -> None:
    target = _NOW + timedelta(seconds=42)
    naive = target.replace(tzinfo=None)  # naive ISO -> v2 must UTC-coerce
    sched = NodeSchedule(name="o", schedule_type=ScheduleType.ONCE, run_at=naive.isoformat())
    nxt = compute_next_fire_time(sched, _NOW)
    assert nxt == target


def test_compute_next_fire_cron_falls_through_to_interval() -> None:
    sched = NodeSchedule(
        name="c",
        schedule_type=ScheduleType.CRON,
        cron="*/5 * * * *",
        interval_s=300,
    )
    nxt = compute_next_fire_time(sched, _NOW)
    assert (nxt - _NOW).total_seconds() == 300


# ---------------------------------------------------------------------------
# 4-7. lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_for_org_empty() -> None:
    sched_obj, _d, _s, _p = _make_scheduler()
    await sched_obj.start_for_org("o", ["n"])
    assert sched_obj._tasks == {}
    await sched_obj.stop_all()


@pytest.mark.asyncio
async def test_start_for_org_registers_enabled_schedule() -> None:
    sched_obj, _d, store, _p = _make_scheduler()
    s = NodeSchedule(name="daily", schedule_type=ScheduleType.INTERVAL, interval_s=3600)
    store.save_node_schedules("o", "n", [s])
    await sched_obj.start_for_org("o", ["n"])
    assert len(sched_obj._tasks) == 1
    key = next(iter(sched_obj._tasks))
    assert key == f"o:n:{s.id}"
    await sched_obj.stop_all()


@pytest.mark.asyncio
async def test_start_for_org_multi_node_multi_schedule() -> None:
    sched_obj, _d, store, _p = _make_scheduler()
    for nid in ("n1", "n2", "n3"):
        for i in range(2):
            store.save_node_schedules(
                "o",
                nid,
                store.get_node_schedules("o", nid)
                + [
                    NodeSchedule(
                        name=f"{nid}-{i}",
                        schedule_type=ScheduleType.INTERVAL,
                        interval_s=3600,
                    )
                ],
            )
    await sched_obj.start_for_org("o", ["n1", "n2", "n3"])
    assert len(sched_obj._tasks) == 6
    await sched_obj.stop_all()
    assert sched_obj._tasks == {}


@pytest.mark.asyncio
async def test_disabled_schedule_not_started() -> None:
    sched_obj, _d, store, _p = _make_scheduler()
    s_on = NodeSchedule(name="on", interval_s=3600, enabled=True)
    s_off = NodeSchedule(name="off", interval_s=3600, enabled=False)
    store.save_node_schedules("o", "n", [s_on, s_off])
    await sched_obj.start_for_org("o", ["n"])
    assert len(sched_obj._tasks) == 1
    assert f"o:n:{s_on.id}" in sched_obj._tasks
    await sched_obj.stop_all()


# ---------------------------------------------------------------------------
# 8-9. cancel / reload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_for_org_cancels_only_that_org() -> None:
    sched_obj, _d, store, _p = _make_scheduler()
    for org in ("o1", "o2"):
        store.save_node_schedules(
            org,
            "n",
            [NodeSchedule(name=org, interval_s=3600)],
        )
        await sched_obj.start_for_org(org, ["n"])
    assert len(sched_obj._tasks) == 2
    await sched_obj.stop_for_org("o1")
    remaining = list(sched_obj._tasks)
    assert len(remaining) == 1
    assert remaining[0].startswith("o2:")
    await sched_obj.stop_all()


@pytest.mark.asyncio
async def test_reload_replaces_tasks_for_node() -> None:
    sched_obj, _d, store, _p = _make_scheduler()
    s_old = NodeSchedule(name="old", interval_s=3600)
    store.save_node_schedules("o", "n", [s_old])
    await sched_obj.start_for_org("o", ["n"])
    old_key = f"o:n:{s_old.id}"
    assert old_key in sched_obj._tasks

    s_new = NodeSchedule(name="new", interval_s=3600)
    store.save_node_schedules("o", "n", [s_new])  # replace list
    await sched_obj.reload_node_schedules("o", "n")
    assert old_key not in sched_obj._tasks
    new_key = f"o:n:{s_new.id}"
    assert new_key in sched_obj._tasks
    await sched_obj.stop_all()


# ---------------------------------------------------------------------------
# 10. concurrency stress -- Nit-2 fold-in (N x 100 ops)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_reload_no_loss_100_ops() -> None:
    """4 worker coroutines x 25 reload cycles each = 100 concurrent reloads.

    The G-RC-9.2 Nit-2 audit flagged the P9.2 concurrent test
    as too weak (2 x 5 inserts = 10 ops). NodeScheduler is
    pure-asyncio so this is a 4-coroutine, ``asyncio.gather``
    stress; every reload cycle cancels the old task and starts
    a fresh one. After the gather, exactly one task must
    remain per node (no leaks, no losses).
    """
    sched_obj, _d, store, _p = _make_scheduler()
    s = NodeSchedule(name="int", interval_s=3600)
    store.save_node_schedules("o", "n", [s])
    await sched_obj.start_for_org("o", ["n"])

    async def worker() -> None:
        for _ in range(25):
            await sched_obj.reload_node_schedules("o", "n")

    await asyncio.gather(*(worker() for _ in range(4)))
    # Exactly one task survives (the latest reload), keyed on s.id
    assert len(sched_obj._tasks) == 1
    assert f"o:n:{s.id}" in sched_obj._tasks
    await sched_obj.stop_all()


# ---------------------------------------------------------------------------
# 11-12. dispatch / trigger_once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_once_invokes_dispatcher_with_v1_prompt() -> None:
    sched_obj, disp, store, probe = _make_scheduler()
    s = NodeSchedule(
        name="hourly",
        schedule_type=ScheduleType.INTERVAL,
        interval_s=3600,
        prompt="check",
        report_condition="on_issue",
        report_to="boss",
    )
    store.save_node_schedules("o", "n", [s])
    res = await sched_obj.trigger_once("o", "n", s.id)
    assert res == {"result": "ok"}
    assert len(disp.calls) == 1
    captured_prompt = disp.calls[0][2]
    expected_prompt_no_ts = build_schedule_prompt(s).split("\n")
    captured_no_ts = [
        line for line in captured_prompt.split("\n") if not line.startswith("\u65f6\u95f4: ")
    ]
    expected_no_ts = [
        line for line in expected_prompt_no_ts if not line.startswith("\u65f6\u95f4: ")
    ]
    assert captured_no_ts == expected_no_ts
    # Two lifecycle events
    event_types = [e[0] for e in probe.events]
    assert event_types == ["schedule_triggered", "schedule_completed"]
    # State persisted back to store
    after = store.get_node_schedules("o", "n")
    assert after[0].last_run_at is not None
    assert after[0].last_result_summary == "ok"


@pytest.mark.asyncio
async def test_trigger_once_missing_schedule_id_returns_error() -> None:
    sched_obj, disp, store, probe = _make_scheduler()
    store.save_node_schedules("o", "n", [NodeSchedule(name="x", interval_s=3600)])
    fake_id = new_schedule_id()
    res = await sched_obj.trigger_once("o", "n", fake_id)
    assert "error" in res
    # No dispatch / no events on miss
    assert disp.calls == []
    assert probe.events == []
