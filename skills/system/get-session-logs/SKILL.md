---
name: get-session-logs
description: Get current session system logs. IMPORTANT - when commands fail, encounter errors, or need to understand previous operation results, call this tool. Logs contain command details, error info, system status.
system: true
handler: system
tool-name: get_session_logs
category: System
---

# Get Session Logs

Get system logs for the current session.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| count | integer | No | Number of log entries to return, default 20，Maximum 200 |
| level | string | No | 过滤日志级别：DEBUG, INFO, WARNING, ERROR |

## When to Use

1. 命令Returns错误码
2. 操作没有预期效果
3. 需要了解之前发生了什么

## Returns

- 命令Execute详情
- 错误信息
- 系统状态
