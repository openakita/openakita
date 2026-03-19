---
name: get-todo-status
description: Get the current plan execution status. Shows all steps and their completion status. Use to check progress during multi-step task execution.
system: true
handler: plan
tool-name: get_todo_status
category: Plan
---

# Get Todo Status

获取当前计划的执行状态。

## Parameters

无需参数。

## Returns

- 计划总览（task_summary）
- 各步骤状态
- 已完成/待执行数量
- 执行日志

## Related Skills

- `create-todo`: 创建计划
- `update-todo-step`: 更新步骤状态
- `complete-todo`: 完成计划
