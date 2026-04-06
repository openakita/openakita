from openakita.scheduler.task import (
    ScheduledTask,
    TaskDurability,
    TaskSource,
    TriggerType,
)


def test_scheduled_task_roundtrip_preserves_source_and_durability():
    task = ScheduledTask.create(
        name="test-task",
        description="desc",
        trigger_type=TriggerType.INTERVAL,
        trigger_config={"interval_minutes": 15},
        prompt="do something",
        task_source=TaskSource.CHAT,
        durability=TaskDurability.PERSISTENT,
    )

    restored = ScheduledTask.from_dict(task.to_dict())
    assert restored.task_source == TaskSource.CHAT
    assert restored.durability == TaskDurability.PERSISTENT
