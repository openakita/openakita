---
name: set-task-timeout
description: Adjust current task timeout policy. Use when the task is expected to take long, or when the system is too aggressive switching models. Prefer increasing timeout for long-running tasks with steady progress.
system: true
handler: system
tool-name: set_task_timeout
category: System
---

# Set Task Timeout

动态调整当前任务的超时策略，主要用于避免"卡死检测"误触发。

## When to Use

- 长任务开始前，预防超时警告
- 发现任务被频繁触发超时警告时

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| progress_timeout_seconds | integer | 是 | 无进展超时阈值（秒），建议 600~3600 |
| hard_timeout_seconds | integer | 否 | 硬超时上限（秒，0=禁用） |
| reason | string | 是 | 简要说明调整原因 |

## Examples

**长时间浏览器任务**:
```json
{
  "progress_timeout_seconds": 1800,
  "reason": "需要完成多步浏览器操作，预计耗时较长"
}
```

## Note

该设置只影响当前会话正在执行的任务，不影响全局配置。

## Related Skills

- `create-plan`: 创建任务计划
- `enable-thinking`: 启用深度思考
