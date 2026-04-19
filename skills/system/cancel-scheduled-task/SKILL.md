---
name: cancel-scheduled-task
description: PERMANENTLY DELETE scheduled task. When user says 'cancel/delete task' use this. When user says 'turn off notification' use update_scheduled_task with notify=false. When user says 'pause task' use update_scheduled_task with enabled=false.
system: true
handler: scheduled
tool-name: cancel_scheduled_task
category: Scheduled Tasks
---

# Cancel Scheduled Task

[delete].

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| task_id | string | Yes | ID |

## Important

****:
- "/delete" →
- "Close" → `update_scheduled_task` notify=false
- "Pause" → `update_scheduled_task` enabled=false

****:deleteResume!

## Related Skills

- `list-scheduled-tasks`: get ID
- `update-scheduled-task`: Set
