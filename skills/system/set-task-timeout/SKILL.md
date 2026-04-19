---
name: set-task-timeout
description: Adjust current task timeout policy. Use when the task is expected to take long, or when the system is too aggressive switching models. Prefer increasing timeout for long-running tasks with steady progress.
system: true
handler: system
tool-name: set_task_timeout
category: System
---

# Set Task Timeout

current task, needUsed for"". 

## When to Use

-, 
-

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| progress_timeout_seconds | integer | Yes | (), 600~3600 |
| hard_timeout_seconds | integer | No | (, 0=Disable) |
| reason | string | Yes | need |

## Examples

****:
```json
{
 "progress_timeout_seconds": 1800,
"reason": "needBrowser operations, "
}
```

## Note

SetwillinExecute, not. 

## Related Skills

- `create-todo`: create
- `enable-thinking`: Enable deep thinking