---
name: get-session-logs
description: Get current session system logs. IMPORTANT - when commands fail, encounter errors, or need to understand previous operation results, call this tool. Logs contain command details, error info, system status.
system: true
handler: system
tool-name: get_session_logs
category: System
---

# Get Session Logs

获取当前会话的系统日志。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| count | integer | 否 | 返回的日志条数，默认 20，最大 200 |
| level | string | 否 | 过滤日志级别：DEBUG, INFO, WARNING, ERROR |

## When to Use

1. 命令返回错误码
2. 操作没有预期效果
3. 需要了解之前发生了什么

## Returns

- 命令执行详情
- 错误信息
- 系统状态
