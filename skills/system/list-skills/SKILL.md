---
name: list-skills
description: List all installed skills following Agent Skills specification. When you need to check available skills, find skill for a task, or verify skill installation.
system: true
handler: skills
tool-name: list_skills
category: Skills Management
---

# List Skills

list已install的技能（遵循 Agent Skills 规范）。

## Parameters

无参数。

## Returns

- 技能名称
- 技能描述
- 是否可自动调用
- 系统技能 vs 外部技能标识

## Related Skills

- `get-skill-info`: get技能详情
- `install-skill`: install新技能
