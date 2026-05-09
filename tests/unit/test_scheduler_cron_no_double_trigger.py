"""Regression test: cron tasks must not double-trigger when completing
slightly before their official slot due to scheduler advance_seconds.

背景见 plans/scheduler-double-trigger-fix —— 09:00 的 cron 任务在 08:59:44
被 advance 触发、08:59:53 完成时，老逻辑用 datetime.now() 调
trigger.get_next_run_time() 会算出"今天 09:00"，下一轮调度循环立刻再触发
一次（双跑 Bug）。本测试覆盖修复后该路径，确保下一次执行时间被推到
真正的下一个 cron 槽（次日 09:00），不会落在当前 advance 窗口内。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from openakita.scheduler.scheduler import TaskScheduler
from openakita.scheduler.task import ScheduledTask, TaskStatus, TriggerType
from openakita.scheduler.triggers import CronTrigger


def _make_daily_9am_task() -> ScheduledTask:
    return ScheduledTask.create(
        name="daily-9am-cron",
        description="每天早上 9 点的整点任务",
        trigger_type=TriggerType.CRON,
        trigger_config={"cron": "0 9 * * *"},
        prompt="hello",
    )


def test_cron_trigger_today_slot_self_match_reproduces_old_bug():
    """Sanity check: 老逻辑用 now=08:59:53 调 CronTrigger，会得到今天 09:00。

    这是 Bug 触发的真正源头 —— 旧 _execute_task 成功分支直接传 now，
    cron `0 9 * * *` 内部 `start = now + 1min → replace(s=0,ms=0) = 09:00:53
    → 09:00`，由于 cron 在 09:00 就匹配，结果返回今天 09:00。
    """
    trigger = CronTrigger("0 9 * * *")
    today_09 = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    just_before_9 = today_09 - timedelta(seconds=7)  # 08:59:53

    next_run = trigger.get_next_run_time(just_before_9)

    assert next_run == today_09, (
        f"老逻辑确实把 next_run 算回今天 09:00（{today_09}），"
        f"实际拿到 {next_run} —— 这正是双触发 Bug 的根因"
    )


def test_cron_next_run_using_advance_window_baseline_skips_today():
    """Fix 后：以 now + advance_seconds + 5s 为基线，cron 必然跳到次日。"""
    trigger = CronTrigger("0 9 * * *")
    today_09 = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    just_before_9 = today_09 - timedelta(seconds=7)  # 08:59:53
    advance_seconds = 20

    baseline = just_before_9 + timedelta(seconds=advance_seconds + 5)  # 09:00:18

    next_run = trigger.get_next_run_time(baseline)

    tomorrow_09 = today_09 + timedelta(days=1)
    assert next_run == tomorrow_09, (
        f"修复后必须跳到次日 09:00（{tomorrow_09}），"
        f"实际拿到 {next_run}"
    )


@pytest.mark.asyncio
async def test_execute_task_success_path_does_not_reschedule_in_current_window(tmp_path: Path):
    """端到端：调度器跑完 cron 任务后，next_run 必须落在 advance 窗口之外。

    这是核心回归断言 —— next_run > now + advance_seconds + jitter，
    所以 _scheduler_loop 下一轮不会再次触发。
    """

    today_09 = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    fake_now_at_complete = today_09 - timedelta(seconds=7)  # 08:59:53

    async def _executor(task: ScheduledTask) -> tuple[bool, str]:
        return True, "ok"

    advance_seconds = 20
    scheduler = TaskScheduler(
        storage_path=tmp_path,
        executor=_executor,
        advance_seconds=advance_seconds,
        check_interval_seconds=2,
    )

    task = _make_daily_9am_task()
    await scheduler.add_task(task)

    # 把 next_run 强制设回今天 09:00，模拟"刚被 advance 触发"
    task.next_run = today_09
    task.status = TaskStatus.SCHEDULED

    # patch datetime.now so _execute_task's timing logic sees pre-9am
    # Use wraps so attribute access like datetime.max still passes through
    # to the real class.
    with patch("openakita.scheduler.scheduler.datetime", wraps=datetime) as mock_dt:
        mock_dt.now.return_value = fake_now_at_complete

        await scheduler._execute_task(task)

    assert task.status == TaskStatus.SCHEDULED, (
        f"成功后任务应回到 SCHEDULED，实际 {task.status}"
    )
    assert task.next_run is not None, "成功后必须有 next_run"

    tomorrow_09 = today_09 + timedelta(days=1)
    assert task.next_run == tomorrow_09, (
        f"修复后 next_run 必须是次日 09:00（{tomorrow_09}），"
        f"实际 {task.next_run} —— Bug 复现，下一轮调度循环又会立刻触发！"
    )

    # 双保险：模拟下一轮 _scheduler_loop 的判断条件，确认不会再次触发
    jitter = TaskScheduler._deterministic_jitter(task.id)
    next_trigger_time = task.next_run - timedelta(seconds=advance_seconds - jitter)
    # _scheduler_loop 的 now = 08:59:54（再过 1s）
    sim_loop_now = fake_now_at_complete + timedelta(seconds=1)
    assert sim_loop_now < next_trigger_time, (
        f"下一轮 loop now={sim_loop_now} 不应再 ≥ 触发时刻 {next_trigger_time}"
    )


@pytest.mark.asyncio
async def test_execute_task_success_clears_missed_count(tmp_path: Path):
    """成功跑完后，metadata 里的历史 missed_count 必须清零。"""

    async def _executor(task: ScheduledTask) -> tuple[bool, str]:
        return True, "ok"

    scheduler = TaskScheduler(
        storage_path=tmp_path,
        executor=_executor,
        advance_seconds=20,
    )

    task = _make_daily_9am_task()
    task.metadata["missed_count"] = 36  # 模拟历史包袱
    await scheduler.add_task(task)

    await scheduler._execute_task(task)

    assert task.metadata.get("missed_count") == 0, (
        f"成功后 missed_count 应被清零，实际 {task.metadata.get('missed_count')}"
    )
    assert "missed_count_cleared_at" in task.metadata, (
        "应记录清零时间戳，便于历史排查"
    )


@pytest.mark.asyncio
async def test_execute_task_failure_does_not_clear_missed_count(tmp_path: Path):
    """失败路径不能误清 missed_count（避免掩盖问题）。"""

    async def _executor(task: ScheduledTask) -> tuple[bool, str]:
        return False, "boom"

    scheduler = TaskScheduler(
        storage_path=tmp_path,
        executor=_executor,
        advance_seconds=20,
    )

    task = _make_daily_9am_task()
    task.metadata["missed_count"] = 12
    await scheduler.add_task(task)

    await scheduler._execute_task(task)

    assert task.metadata.get("missed_count") == 12, (
        "失败路径不应清零 missed_count"
    )


if __name__ == "__main__":
    asyncio.run(test_execute_task_success_path_does_not_reschedule_in_current_window(
        Path("/tmp/test_scheduler_cron")
    ))
