---
name: set-task-timeout
description: Adjust current task timeout policy. Use when the task is expected to take long, or when the system is too aggressive switching models. Prefer increasing timeout for long-running tasks with steady progress.
system: true
handler: system
tool-name: set_task_timeout
category: System
---

# Set Task Timeout

动态调整current task的超时策略，主要Used for避免"卡死检测"误触发。

## When to Use

- 长任务开始前，预防超时警告
- 发现任务被频繁触发超时警告时

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| progress_timeout_seconds | integer | Yes | 无进展超时阈值（秒），建议 600~3600 |
| hard_timeout_seconds | integer | No | 硬超时上限（秒，0=Disable） |
| reason | string | Yes | 简要说明调整原因 |

## Examples

**长时间浏览器任务**:
```json
{
  "progress_timeout_seconds": 1800,
  "reason": "需要完成多步Browser operations，预计耗时较长"
}
```

## Note

该Set只影响当前会话正在Execute的任务，不影响全局配置。

## Related Skills

- `create-todo`: create任务计划
- `enable-thinking`: Enable deep thinking
