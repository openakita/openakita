---
name: schedule-task
description: Create scheduled task or reminder. IMPORTANT - must actually call this tool to create task. Just saying 'OK I will remind you' does NOT create the task. Task types - (1) reminder for simple messages, (2) task for AI operations.
system: true
handler: scheduled
tool-name: schedule_task
category: Scheduled Tasks
---

# Schedule Task

createor. 

## Important

**Callcreate! Yes" will"notwillcreate! **

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| name | string | Yes | |
| description | string | Yes | |
| task_type | string | Yes |: reminder or task |
| trigger_type | string | Yes |: once, interval, cron |
| trigger_config | object | Yes | () |
| reminder_message | string | No | ( reminder ) |
| prompt | string | No | AI Execute ( task ) |

## Task Type Guidelines

**90% allYes reminder! **

✅ **reminder** (Default):
- "" → reminder
- "" → reminder
- "" → reminder

❌ **task** (need AI Execute):
- "" → task (need) 
- "" → task (need) 

## Trigger Config

**once () **:
```json
{"run_at": "2026-02-01 10:00"}
```

**interval (Execute) **:
```json
{"interval_minutes": 30}
```

**cron (cron ) **:
```json
{"cron": "0 9 * * *"}
```

## Examples

****:
```json
{
"name": "",
"description": "",
 "task_type": "reminder",
 "trigger_type": "interval",
 "trigger_config": {"interval_minutes": 60},
"reminder_message": "! "
}
```

****:
```json
{
"name": "",
"description": "9",
 "task_type": "task",
 "trigger_type": "cron",
 "trigger_config": {"cron": "0 9 * * *"},
"prompt": " "
}
```

## Related Skills

- `list-scheduled-tasks`: List alreadycreate
- `cancel-scheduled-task`: