---
name: update-plan-step
description: Update the status of a plan step. MUST call after completing each step to track progress. Status values - pending, in_progress, completed, failed, skipped.
system: true
handler: plan
tool-name: update_plan_step
category: Plan
---

# Update Plan Step

更新计划中某个步骤的状态。每完成一步必须调用。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| step_id | string | 是 | 步骤 ID |
| status | string | 是 | pending / in_progress / completed / failed / skipped |
| result | string | 否 | 执行结果或错误信息 |

## Examples

**步骤完成**:
```json
{
  "step_id": "step_1",
  "status": "completed",
  "result": "已打开百度首页"
}
```

**步骤失败**:
```json
{
  "step_id": "step_2",
  "status": "failed",
  "result": "找不到搜索框元素"
}
```

## Related Skills

- `create-plan`: 创建计划
- `get-plan-status`: 查看计划状态
- `complete-plan`: 完成计划
