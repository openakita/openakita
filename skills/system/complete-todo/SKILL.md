---
name: complete-todo
description: Mark the plan as completed and generate a summary report. Call when ALL steps are done. Returns execution summary with success/failure statistics.
system: true
handler: plan
tool-name: complete_todo
category: Plan
---

# Complete Todo

Mark plan as completed, Generate final report.Called after all steps are completed.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| summary | string | Yes | |

## Examples

```json
{
"summary": "searchSend"
}
```

## Returns

- Executeneed
- Success/failure statistics
- Total time elapsed

## Related Skills

- `create-todo`: create
- `update-todo-step`: updateStep status
- `get-todo-status`: View
