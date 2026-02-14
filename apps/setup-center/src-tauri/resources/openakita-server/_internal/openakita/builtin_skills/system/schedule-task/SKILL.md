---
name: schedule-task
description: Create scheduled task or reminder. IMPORTANT - must actually call this tool to create task. Just saying 'OK I will remind you' does NOT create the task. Task types - (1) reminder for simple messages, (2) task for AI operations.
system: true
handler: scheduled
tool-name: schedule_task
category: Scheduled Tasks
---

# Schedule Task

创建定时任务或提醒。

## Important

**必须调用此工具才能创建任务！只是说"好的我会提醒你"不会创建任务！**

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| name | string | 是 | 任务名称 |
| description | string | 是 | 任务描述 |
| task_type | string | 是 | 任务类型：reminder 或 task |
| trigger_type | string | 是 | 触发类型：once, interval, cron |
| trigger_config | object | 是 | 触发配置（见下方） |
| reminder_message | string | 否 | 提醒消息（仅 reminder 类型） |
| prompt | string | 否 | AI 执行提示（仅 task 类型） |

## Task Type Guidelines

**90% 的提醒都应该是 reminder 类型！**

✅ **reminder**（默认优先）:
- "提醒我喝水" → reminder
- "站立提醒" → reminder
- "叫我起床" → reminder

❌ **task**（仅当需要 AI 执行操作时）:
- "查询天气告诉我" → task（需要查询）
- "截图发给我" → task（需要操作）

## Trigger Config

**once（一次性）**:
```json
{"run_at": "2026-02-01 10:00"}
```

**interval（间隔执行）**:
```json
{"interval_minutes": 30}
```

**cron（cron 表达式）**:
```json
{"cron": "0 9 * * *"}
```

## Examples

**每小时喝水提醒**:
```json
{
  "name": "喝水提醒",
  "description": "每小时提醒喝水",
  "task_type": "reminder",
  "trigger_type": "interval",
  "trigger_config": {"interval_minutes": 60},
  "reminder_message": "该喝水了！"
}
```

**每天早上查天气**:
```json
{
  "name": "天气播报",
  "description": "每天早上9点查询天气",
  "task_type": "task",
  "trigger_type": "cron",
  "trigger_config": {"cron": "0 9 * * *"},
  "prompt": "查询今天的天气并告诉我"
}
```

## Related Skills

- `list-scheduled-tasks`: 查看已创建的任务
- `cancel-scheduled-task`: 取消任务
