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
| enabled_only | boolean | No | Whether to only list enabled tasks，Default false |

## Returns

- Task ID
- 名称
- 类型（reminder/task）
- 状态（enabled/disabled）
- 下次Execute时间

## Examples

**List all任务**:
```json
{}
```

**只ListEnable的任务**:
```json
{"enabled_only": true}
```

## Related Skills

- `schedule-task`: Create新任务
- `cancel-scheduled-task`: 取消任务
- `update-scheduled-task`: Update任务Set
