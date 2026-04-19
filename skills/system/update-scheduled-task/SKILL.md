---
name: update-scheduled-task
description: Modify scheduled task settings WITHOUT deleting. Can modify notify_on_start, notify_on_complete, enabled. Common uses - (1) 'Turn off notification' = notify=false, (2) 'Pause task' = enabled=false, (3) 'Resume task' = enabled=true.
system: true
handler: scheduled
tool-name: update_scheduled_task
category: Scheduled Tasks
---

# Update Scheduled Task

Set[notdelete].

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| task_id | string | Yes | need ID |
| notify_on_start | boolean | No | YesNo, not=not |
| notify_on_complete | boolean | No | YesNo, not=not |
| enabled | boolean | No | Enable/Pause, not=not |

## Common Uses

- "Close" → notify_on_start=false, notify_on_complete=false
- "Pause" → enabled=false
- "Resume" → enabled=true

## Related Skills

- `list-scheduled-tasks`: get ID
- `cancel-scheduled-task`: delete
