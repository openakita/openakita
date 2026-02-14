---
name: complete-plan
description: Mark the plan as completed and generate a summary report. Call when ALL steps are done. Returns execution summary with success/failure statistics.
system: true
handler: plan
tool-name: complete_plan
category: Plan
---

# Complete Plan

标记计划完成，生成最终报告。在所有步骤完成后调用。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| summary | string | 是 | 完成总结 |

## Examples

```json
{
  "summary": "已完成百度搜索天气并截图发送给用户"
}
```

## Returns

- 执行摘要
- 成功/失败统计
- 总耗时

## Related Skills

- `create-plan`: 创建计划
- `update-plan-step`: 更新步骤状态
- `get-plan-status`: 查看计划状态
