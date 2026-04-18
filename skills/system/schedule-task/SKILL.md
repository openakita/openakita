---
name: schedule-task
description: Create scheduled task or reminder. IMPORTANT - must actually call this tool to create task. Just saying 'OK I will remind you' does NOT create the task. Task types - (1) reminder for simple messages, (2) task for AI operations.
system: true
handler: scheduled
tool-name: schedule_task
category: Scheduled Tasks
---

# Schedule Task

create定时任务或提醒。

## Important

**必须Call此工具才能create任务！只Yes说"好的我会提醒你"不会create任务！**

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| name | string | Yes | 任务名称 |
| description | string | Yes | 任务描述 |
| task_type | string | Yes | 任务类型：reminder 或 task |
| trigger_type | string | Yes | 触发类型：once, interval, cron |
| trigger_config | object | Yes | 触发配置（见下方） |
| reminder_message | string | No | 提醒消息（仅 reminder 类型） |
| prompt | string | No | AI Execute提示（仅 task 类型） |

## Task Type Guidelines

**90% 的提醒都应该Yes reminder 类型！**

✅ **reminder**（Default优先）:
- "提醒我喝水" → reminder
- "站立提醒" → reminder
- "叫我起床" → reminder

❌ **task**（仅当需要 AI Execute操作时）:
- "查询天气告诉我" → task（需要查询）
- "截图发给我" → task（需要操作）

## Trigger Config

**once（一次性）**:
```json
{"run_at": "2026-02-01 10:00"}
```

**interval（间隔Execute）**:
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

- `list-scheduled-tasks`: List alreadycreate的任务
- `cancel-scheduled-task`: 取消任务
