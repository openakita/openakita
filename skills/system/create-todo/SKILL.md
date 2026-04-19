---
name: create-todo
description: "MUST CALL FIRST for multi-step tasks! If user request needs 2+ tool calls (like 'open + search + screenshot'), call create_todo BEFORE any other tool."
system: true
handler: plan
tool-name: create_todo
category: Plan
---

# Create Todo

createExecute. createExecute. 

## When to Use

- Taskneed 2
- have"", "", ""
-

## Workflow

1. `create-todo` → 2. Execute → 3. `update-todo-step` → 4.... → 5. `complete-todo`

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| task_summary | string | Yes | |
| steps | array | Yes | |

### Step Item

| | Type | Required | Description |
|-----|------|-----|------|
| id | string | Yes | ID ( step_1) |
| description | string | Yes | |
| tool | string | No | Use |
| skills | array | No | skill (Optional,Used for) |
| depends_on | array | No | ID |

## Examples

**Opensearch**:
```json
{
"task_summary": "OpensearchSend",
 "steps": [
{"id": "step_1", "description": "Open", "tool": "browser_navigate", "skills": ["browser-navigate"]},
{"id": "step_2", "description": "search", "tool": "browser_type", "skills": ["browser-type"], "depends_on": ["step_1"]},
{"id": "step_3", "description": "", "tool": "browser_screenshot", "skills": ["browser-screenshot"], "depends_on": ["step_2"]},
{"id": "step_4", "description": "Send", "tool": "deliver_artifacts", "skills": ["deliver-artifacts"], "depends_on": ["step_3"]}
 ]
}
```

## Related Skills

- `update-todo-step`: updateStep status
- `get-todo-status`: View
- `complete-todo`: