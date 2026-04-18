---
name: update-scheduled-task
description: Modify scheduled task settings WITHOUT deleting. Can modify notify_on_start, notify_on_complete, enabled. Common uses - (1) 'Turn off notification' = notify=false, (2) 'Pause task' = enabled=false, (3) 'Resume task' = enabled=true.
system: true
handler: scheduled
tool-name: update_scheduled_task
category: Scheduled Tasks
---

# Update Scheduled Task

修改定时任务Set【不delete任务】。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| task_id | string | Yes | 要修改的任务 ID |
| notify_on_start | boolean | No | 开始时YesNo通知，不传=不修改 |
| notify_on_complete | boolean | No | 完成时YesNo通知，不传=不修改 |
| enabled | boolean | No | Enable/Pause任务，不传=不修改 |

## Common Uses

- "Close提醒" → notify_on_start=false, notify_on_complete=false
- "Pause任务" → enabled=false
- "Resume任务" → enabled=true

## Related Skills

- `list-scheduled-tasks`: get任务 ID
- `cancel-scheduled-task`: 永久delete任务
