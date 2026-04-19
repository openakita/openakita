---
name: list-scheduled-tasks
description: List all scheduled tasks with their ID, name, type, status, and next execution time. When you need to check existing tasks, find task ID for cancel/update, or verify task creation.
system: true
handler: scheduled
tool-name: list_scheduled_tasks
category: Scheduled Tasks
---

# List Scheduled Tasks

List all scheduled tasks.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| enabled_only | boolean | No | Whether to only list enabled tasks, Default false |

## Returns

- Task ID
-
- (reminder/task)
- (enabled/disabled)
- Execute

## Examples

**List all**:
```json
{}
```

**ListEnable **:
```json
{"enabled_only": true}
```

## Related Skills

- `schedule-task`: Create
- `cancel-scheduled-task`:
- `update-scheduled-task`: UpdateSet
