---
name: update-scheduled-task
description: Modify scheduled task settings WITHOUT deleting. Can modify notify_on_start, notify_on_complete, enabled. Common uses - (1) 'Turn off notification' = notify=false, (2) 'Pause task' = enabled=false, (3) 'Resume task' = enabled=true.
system: true
handler: scheduled
tool-name: update_scheduled_task
category: Scheduled Tasks
---

# Update Scheduled Task

修改定时任务设置【不删除任务】。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| task_id | string | 是 | 要修改的任务 ID |
| notify_on_start | boolean | 否 | 开始时是否通知，不传=不修改 |
| notify_on_complete | boolean | 否 | 完成时是否通知，不传=不修改 |
| enabled | boolean | 否 | 启用/暂停任务，不传=不修改 |

## Common Uses

- "关闭提醒" → notify_on_start=false, notify_on_complete=false
- "暂停任务" → enabled=false
- "恢复任务" → enabled=true

## Related Skills

- `list-scheduled-tasks`: 获取任务 ID
- `cancel-scheduled-task`: 永久删除任务
