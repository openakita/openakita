---
name: trigger-scheduled-task
description: Immediately trigger scheduled task without waiting for scheduled time. When you need to test task execution or run task ahead of schedule.
system: true
handler: scheduled
tool-name: trigger_scheduled_task
category: Scheduled Tasks
---

# Trigger Scheduled Task

立即Trigger scheduled task（不等待计划时间）。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| task_id | string | Yes | 任务 ID |

## Notes

- 不会影响原有的Execute计划
- Applicable to测试任务或提前Run

## Related Skills

- `list-scheduled-tasks`: get任务 ID
- `schedule-task`: create新任务
