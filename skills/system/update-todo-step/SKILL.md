---
name: update-todo-step
description: Update the status of a plan step. MUST call after completing each step to track progress. Status values - pending, in_progress, completed, failed, skipped.
system: true
handler: plan
tool-name: update_todo_step
category: Plan
---

# Update Todo Step

update .Call.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| step_id | string | Yes | ID |
| status | string | Yes | pending / in_progress / completed / failed / skipped |
| result | string | No | Execution result or error message |

## Examples

****:
```json
{
  "step_id": "step_1",
  "status": "completed",
"result": "Open"
}
```

****:
```json
{
  "step_id": "step_2",
  "status": "failed",
"result": "notsearch"
}
```

## Related Skills

- `create-todo`: create
- `get-todo-status`: View
- `complete-todo`:
