"""Regression tests for scheduler.trigger_in_background.

Covers the bug where trigger_in_background pre-added ``task_id`` to
``_running_tasks`` for race safety, but then the inner ``trigger_now``
saw that same marker and short-circuited — causing the API to return
an ``execution_id`` for an execution that never actually ran.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from openakita.scheduler.scheduler import TaskScheduler
from openakita.scheduler.task import ScheduledTask


async def _make_scheduler(
    tmp_path: Path, executor=None, *, max_concurrent: int = 2
) -> TaskScheduler:
    scheduler = TaskScheduler(
        storage_path=tmp_path,
        executor=executor,
        max_concurrent=max_concurrent,
        check_interval_seconds=60,
        advance_seconds=0,
    )
    # Allocate the semaphore without starting the scheduler loop
    # (we only want to exercise trigger_now / trigger_in_background).
    scheduler._semaphore = asyncio.Semaphore(max_concurrent)
    return scheduler


def _make_cron_task(name: str = "manual-task") -> ScheduledTask:
    # A cron that is unlikely to fire during the test window; the actual
    # scheduler loop is not started, so timing does not matter.
    run_at = (datetime.now() + timedelta(hours=1)).isoformat()
    return ScheduledTask.create(
        name=name,
        description="regression",
        trigger_type=__import__(
            "openakita.scheduler.task", fromlist=["TriggerType"]
        ).TriggerType.ONCE,
        trigger_config={"run_at": run_at},
        prompt="noop",
    )


async def _wait_until(
    predicate: Callable[[], bool], *, attempts: int = 100, delay: float = 0.02
) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(delay)


@pytest.mark.asyncio
async def test_trigger_in_background_actually_runs_executor(tmp_path):
    """The backported trigger_in_background used to leave the executor
    un-invoked because it pre-added the task_id to _running_tasks and
    then trigger_now tripped on its own "already running" guard. Guard
    against regression by asserting the executor callback actually fires
    and the returned execution_id ends up in the execution log.
    """
    calls: list[str] = []

    async def executor(task):
        calls.append(task.id)
        return True, "ok"

    scheduler = await _make_scheduler(tmp_path, executor=executor)
    task = _make_cron_task()
    task_id = await scheduler.add_task(task)

    execution_id = scheduler.trigger_in_background(task_id)
    assert execution_id is not None
    assert execution_id.startswith("exec_"), (
        "API must return a real TaskExecution id, not a raw uuid4 "
        "(otherwise /executions/{id} polls always 404)."
    )

    await _wait_until(
        lambda: execution_id in [e.id for e in scheduler._executions]
        and task_id not in scheduler._running_tasks
    )

    assert calls == [task_id], (
        "Executor should have been invoked exactly once. If this fails "
        "the trigger_now running-guard is again short-circuiting "
        "trigger_in_background."
    )

    # The pre-allocated execution_id must also be the one persisted.
    stored_ids = [e.id for e in scheduler._executions]
    assert execution_id in stored_ids, (
        "Pre-allocated execution was not persisted — the returned id "
        "would 404 on /api/scheduler/executions/{id}."
    )

    # Running marker must be released afterwards so a retry can happen.
    assert task_id not in scheduler._running_tasks


@pytest.mark.asyncio
async def test_trigger_in_background_rejects_rapid_retry(tmp_path):
    """Two back-to-back trigger_in_background calls must result in only
    one execution — the second one hits the running guard and returns
    None. This is the race the pre-add was introduced to fix, and it
    must keep working after the skip-guard rewire.
    """
    gate = asyncio.Event()

    async def executor(task):
        # Hold the executor until the second trigger attempt has happened.
        await gate.wait()
        return True, "done"

    scheduler = await _make_scheduler(tmp_path, executor=executor)
    task = _make_cron_task()
    task_id = await scheduler.add_task(task)

    first = scheduler.trigger_in_background(task_id)
    # Yield so the _runner task starts and grabs the semaphore before
    # we issue the second trigger. With check_interval=60 the scheduler
    # loop cannot interfere.
    await asyncio.sleep(0)

    second = scheduler.trigger_in_background(task_id)
    assert first is not None
    assert second is None, (
        "Second rapid trigger must be rejected while the first is still "
        "running — otherwise we duplicate side effects."
    )

    gate.set()
    await _wait_until(lambda: task_id not in scheduler._running_tasks)
    assert task_id not in scheduler._running_tasks


@pytest.mark.asyncio
async def test_trigger_now_still_guards_against_double_run(tmp_path):
    """Regression guard for the sync trigger_now path: the refactor
    introduced a ``_skip_running_check`` knob for the background caller,
    but the default (False) must keep the original "already running"
    protection intact.
    """
    started = asyncio.Event()
    gate = asyncio.Event()

    async def executor(task):
        started.set()
        await gate.wait()
        return True, "ok"

    scheduler = await _make_scheduler(tmp_path, executor=executor)
    task = _make_cron_task()
    task_id = await scheduler.add_task(task)

    first_task = asyncio.create_task(scheduler.trigger_now(task_id))
    await started.wait()

    # The first call is parked inside executor; a second direct call
    # must observe _running_tasks and bail out without running the
    # executor again.
    second = await scheduler.trigger_now(task_id)
    assert second is None

    gate.set()
    result = await first_task
    assert result is not None
    assert result.status == "success"
