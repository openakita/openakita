---
name: update-todo-step
description: Update the status of a plan step. MUST call after completing each step to track progress. Status values - pending, in_progress, completed, failed, skipped.
system: true
handler: plan
tool-name: update_todo_step
category: Plan
---

# Update Todo Step

update计划中某个步骤的状态。每完成一步必须Call。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| step_id | string | Yes | 步骤 ID |
| status | string | Yes | pending / in_progress / completed / failed / skipped |
| result | string | No | Execution result or error message |

## Examples

**步骤完成**:
```json
{
  "step_id": "step_1",
  "status": "completed",
  "result": "已Open百度首页"
}
```

**步骤失败**:
```json
{
  "step_id": "step_2",
  "status": "failed",
  "result": "找不到search框元素"
}
```

## Related Skills

- `create-todo`: create计划
- `get-todo-status`: View计划状态
- `complete-todo`: 完成计划
