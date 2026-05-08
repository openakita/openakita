"""L1 Unit Tests: Scheduled task creation, state transitions, and triggers."""

import asyncio
from datetime import datetime, timedelta

from openakita.scheduler.executor import TaskExecutor
from openakita.scheduler.task import ScheduledTask, TaskStatus, TriggerType
from openakita.scheduler.triggers import CronTrigger, IntervalTrigger, OnceTrigger, Trigger


class TestScheduledTaskCreation:
    def test_create_basic_task(self):
        task = ScheduledTask.create(
            name="test-task",
            description="A test task",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": (datetime.now() + timedelta(hours=1)).isoformat()},
            prompt="Do something",
        )
        assert task.name == "test-task"
        assert task.status == TaskStatus.PENDING
        assert task.enabled is True

    def test_create_reminder(self):
        run_at = datetime.now() + timedelta(hours=2)
        task = ScheduledTask.create_reminder(
            name="birthday-reminder",
            description="Remind about birthday",
            run_at=run_at,
            message="Happy birthday!",
        )
        assert task.is_reminder is True
        assert task.reminder_message == "Happy birthday!"

    def test_create_interval_task(self):
        task = ScheduledTask.create_interval(
            name="hourly-check",
            description="Check every hour",
            interval_minutes=60,
            prompt="Run health check",
        )
        assert task.trigger_type == TriggerType.INTERVAL

    def test_create_cron_task(self):
        task = ScheduledTask.create_cron(
            name="daily-report",
            description="Generate daily report",
            cron_expression="0 8 * * *",
            prompt="Generate report",
        )
        assert task.trigger_type == TriggerType.CRON


class TestTaskStateTransitions:
    def test_enable_disable(self):
        task = ScheduledTask.create(
            name="t", description="d",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": datetime.now().isoformat()},
            prompt="p",
        )
        task.disable()
        assert task.enabled is False
        task.enable()
        assert task.enabled is True

    def test_mark_running(self):
        task = ScheduledTask.create(
            name="t", description="d",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": datetime.now().isoformat()},
            prompt="p",
        )
        task.mark_running()
        assert task.status == TaskStatus.RUNNING

    def test_mark_completed(self):
        task = ScheduledTask.create(
            name="t", description="d",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": datetime.now().isoformat()},
            prompt="p",
        )
        task.mark_running()
        task.mark_completed()
        assert task.status == TaskStatus.COMPLETED
        assert task.run_count == 1

    def test_mark_failed(self):
        task = ScheduledTask.create(
            name="t", description="d",
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": datetime.now().isoformat()},
            prompt="p",
        )
        task.mark_running()
        task.mark_failed("timeout")
        # After failure, task may go to FAILED or back to SCHEDULED for retry
        assert task.status in (TaskStatus.FAILED, TaskStatus.SCHEDULED)
        assert task.fail_count == 1


class TestSystemTaskTimeouts:
    async def test_daily_memory_timeout_is_safe_pause_not_failure(self, monkeypatch):
        async def fake_wait_for(_coro, timeout):
            if hasattr(_coro, "close"):
                _coro.close()
            raise TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
        task = ScheduledTask.create(
            name="daily memory",
            description="daily memory",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"interval_minutes": 60},
            prompt="",
            action="system:daily_memory",
            deletable=False,
        )
        executor = TaskExecutor()

        success, message = await executor._execute_system_task(task)

        assert success is True
        assert "下次" in message


class TestTaskSerialization:
    def test_to_dict_and_back(self):
        task = ScheduledTask.create(
            name="serialize-test", description="Test serialization",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"interval_minutes": 30},
            prompt="Do it",
        )
        d = task.to_dict()
        assert d["name"] == "serialize-test"
        restored = ScheduledTask.from_dict(d)
        assert restored.name == task.name
        assert restored.prompt == task.prompt


class TestTriggers:
    def test_once_trigger_fires_once(self):
        run_at = datetime.now() + timedelta(seconds=-1)
        trigger = OnceTrigger(run_at=run_at)
        assert trigger.should_run() is True
        trigger.mark_fired()
        assert trigger.should_run() is False

    def test_interval_trigger_next_run(self):
        trigger = IntervalTrigger(interval_minutes=60)
        next_run = trigger.get_next_run_time(last_run=datetime.now())
        assert next_run > datetime.now()
        assert (next_run - datetime.now()).total_seconds() < 3700  # ~60 min

    def test_cron_trigger_next_run(self):
        trigger = CronTrigger(cron_expression="0 8 * * *")
        next_run = trigger.get_next_run_time()
        assert next_run is not None
        assert next_run > datetime.now()

    def test_cron_trigger_describe(self):
        trigger = CronTrigger(cron_expression="0 8 * * *")
        desc = trigger.describe()
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_trigger_from_config(self):
        trigger = Trigger.from_config("once", {"run_at": (datetime.now() + timedelta(hours=1)).isoformat()})
        assert isinstance(trigger, OnceTrigger)

