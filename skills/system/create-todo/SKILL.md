---
name: create-todo
description: "MUST CALL FIRST for multi-step tasks! If user request needs 2+ tool calls (like 'open + search + screenshot'), call create_todo BEFORE any other tool."
system: true
handler: plan
tool-name: create_todo
category: Plan
---

# Create Todo

create任务Execute计划。多步骤任务必须先create计划再Execute。

## When to Use

- Task需要超过 2 步完成时
- 用户请求中有"然后"、"接着"、"之后"等词
- 涉及多个工具协作

## Workflow

1. `create-todo` → 2. Execute步骤 → 3. `update-todo-step` → 4. ... → 5. `complete-todo`

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| task_summary | string | Yes | 任务的一句话总结 |
| steps | array | Yes | 步骤列表 |

### Step Item

| 字段 | Type | Required | Description |
|-----|------|-----|------|
| id | string | Yes | 步骤 ID（如 step_1） |
| description | string | Yes | 步骤描述 |
| tool | string | No | 预计Use的工具 |
| skills | array | No | 关联的 skill 名称列表（Optional,Used for追踪） |
| depends_on | array | No | 依赖的步骤 ID |

## Examples

**Open百度search天气并截图发给用户**:
```json
{
  "task_summary": "Open百度search天气并截图Send",
  "steps": [
    {"id": "step_1", "description": "Open百度", "tool": "browser_navigate", "skills": ["browser-navigate"]},
    {"id": "step_2", "description": "输入search关键词", "tool": "browser_type", "skills": ["browser-type"], "depends_on": ["step_1"]},
    {"id": "step_3", "description": "截图", "tool": "browser_screenshot", "skills": ["browser-screenshot"], "depends_on": ["step_2"]},
    {"id": "step_4", "description": "Send截图", "tool": "deliver_artifacts", "skills": ["deliver-artifacts"], "depends_on": ["step_3"]}
  ]
}
```

## Related Skills

- `update-todo-step`: updateStep status
- `get-todo-status`: View计划状态
- `complete-todo`: 完成计划
