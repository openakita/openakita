---
name: get-todo-status
description: Get the current plan execution status. Shows all steps and their completion status. Use to check progress during multi-step task execution.
system: true
handler: plan
tool-name: get_todo_status
category: Plan
---

# Get Todo Status

get当前计划的Execute状态。

## Parameters

No parameters required.

## Returns

- 计划总览（task_summary）
- 各Step status
- 已完成/待Execute数量
- Execute日志

## Related Skills

- `create-todo`: create计划
- `update-todo-step`: updateStep status
- `complete-todo`: 完成计划
