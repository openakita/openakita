---
name: complete-todo
description: Mark the plan as completed and generate a summary report. Call when ALL steps are done. Returns execution summary with success/failure statistics.
system: true
handler: plan
tool-name: complete_todo
category: Plan
---

# Complete Todo

Mark plan as completed，Generate final report。Called after all steps are completed。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| summary | string | Yes | 完成总结 |

## Examples

```json
{
  "summary": "已完成百度search天气并截图Send给用户"
}
```

## Returns

- Execute摘要
- Success/failure statistics
- Total time elapsed

## Related Skills

- `create-todo`: create计划
- `update-todo-step`: updateStep status
- `get-todo-status`: View计划状态
