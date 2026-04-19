---
name: trigger-scheduled-task
description: Immediately trigger scheduled task without waiting for scheduled time. When you need to test task execution or run task ahead of schedule.
system: true
handler: scheduled
tool-name: trigger_scheduled_task
category: Scheduled Tasks
---

# Trigger Scheduled Task

Trigger scheduled task(not).

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| task_id | string | Yes | ID |

## Notes

- notwillhave Execute
- Applicable toorRun

## Related Skills

- `list-scheduled-tasks`: get ID
- `schedule-task`: create
