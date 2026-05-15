"""Tests for OrgNodeScheduler — scheduled task management, smart frequency."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openakita.orgs.node_scheduler import (
    OrgNodeScheduler,
    CLEAN_THRESHOLD,
    FREQUENCY_MULTIPLIER,
    MAX_FREQUENCY_FACTOR,
)
from openakita.orgs.models import NodeSchedule, ScheduleType


@pytest.fixture()
def scheduler(mock_runtime) -> OrgNodeScheduler:
    return OrgNodeScheduler(mock_runtime)


class TestStartStop:
    async def test_start_with_no_schedules(self, scheduler: OrgNodeScheduler, persisted_org):
        await scheduler.start_for_org(persisted_org)
        assert len(scheduler._tasks) == 0

    async def test_start_with_schedules(self, scheduler: OrgNodeScheduler, persisted_org, mock_runtime):
        nid = persisted_org.nodes[0].id
        sched = NodeSchedule(name="巡检", schedule_type=ScheduleType.INTERVAL, interval_s=600, prompt="检查")
        mock_runtime._manager.add_node_schedule(persisted_org.id, nid, sched)

        await scheduler.start_for_org(persisted_org)
        assert len(scheduler._tasks) == 1

        await scheduler.stop_for_org(persisted_org.id)
        assert len(scheduler._tasks) == 0

    async def test_stop_all(self, scheduler: OrgNodeScheduler, persisted_org, mock_runtime):
        nid = persisted_org.nodes[0].id
        sched = NodeSchedule(name="X", schedule_type=ScheduleType.INTERVAL, interval_s=600, prompt="x")
        mock_runtime._manager.add_node_schedule(persisted_org.id, nid, sched)

        await scheduler.start_for_org(persisted_org)
        assert len(scheduler._tasks) >= 1

        await scheduler.stop_all()
        assert len(scheduler._tasks) == 0


class TestReload:
    async def test_reload_node_schedules(self, scheduler: OrgNodeScheduler, persisted_org, mock_runtime):
        nid = persisted_org.nodes[0].id
        sched = NodeSchedule(name="Old", schedule_type=ScheduleType.INTERVAL, interval_s=600, prompt="old")
        mock_runtime._manager.add_node_schedule(persisted_org.id, nid, sched)
        scheduler._start_schedule(persisted_org.id, nid, sched)
        assert len(scheduler._tasks) == 1

        await scheduler.reload_node_schedules(persisted_org.id, nid)
        assert len(scheduler._tasks) == 1
        await scheduler.stop_all()


class TestTriggerOnce:
    async def test_trigger_nonexistent_schedule(self, scheduler: OrgNodeScheduler, persisted_org):
        result = await scheduler.trigger_once(persisted_org.id, "node_ceo", "fake_sched")
        assert "error" in result

    async def test_trigger_once_success(self, scheduler: OrgNodeScheduler, persisted_org, mock_runtime):
        nid = persisted_org.nodes[0].id
        sched = NodeSchedule(name="手动触发", schedule_type=ScheduleType.INTERVAL, interval_s=3600, prompt="检查状态")
        mock_runtime._manager.add_node_schedule(persisted_org.id, nid, sched)

        mock_runtime.send_command = AsyncMock(return_value={"result": "检查完成"})
        result = await scheduler.trigger_once(persisted_org.id, nid, sched.id)
        assert result == {"result": "检查完成"}

        mock_runtime.send_command.assert_awaited_once()
        prompt = mock_runtime.send_command.call_args[0][2]
        assert "手动触发" in prompt or "检查状态" in prompt

    async def test_trigger_updates_schedule_state(self, scheduler: OrgNodeScheduler, persisted_org, mock_runtime):
        nid = persisted_org.nodes[0].id
        sched = NodeSchedule(name="追踪", prompt="x")
        mock_runtime._manager.add_node_schedule(persisted_org.id, nid, sched)

        mock_runtime.send_command = AsyncMock(return_value={"result": "done"})
        await scheduler.trigger_once(persisted_org.id, nid, sched.id)

        updated = mock_runtime._manager.get_node_schedules(persisted_org.id, nid)
        assert len(updated) == 1
        assert updated[0].last_run_at is not None
        assert updated[0].last_result_summary is not None

    async def test_trigger_emits_events(self, scheduler: OrgNodeScheduler, persisted_org, mock_runtime):
        nid = persisted_org.nodes[0].id
        sched = NodeSchedule(name="事件测试", prompt="x")
        mock_runtime._manager.add_node_schedule(persisted_org.id, nid, sched)

        mock_runtime.send_command = AsyncMock(return_value={"result": "ok"})
        await scheduler.trigger_once(persisted_org.id, nid, sched.id)

        es = mock_runtime.get_event_store()
        triggered = es.query(event_type="schedule_triggered")
        assert len(triggered) >= 1
        completed = es.query(event_type="schedule_completed")
        assert len(completed) >= 1


class TestConstants:
    def test_threshold_values(self):
        assert CLEAN_THRESHOLD > 0
        assert FREQUENCY_MULTIPLIER > 1.0
        assert MAX_FREQUENCY_FACTOR > 1.0

