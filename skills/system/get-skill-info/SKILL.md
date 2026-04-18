---
name: get-skill-info
description: Get skill detailed instructions and usage guide (Level 2 disclosure). When you need to understand how to use a skill, check skill capabilities, or learn skill parameters.
system: true
handler: skills
tool-name: get_skill_info
category: Skills Management
---

# Get Skill Info

get技能的详细信息和指令（Level 2 披露）。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| skill_name | string | Yes | Skill name |

## Returns

- Full的 SKILL.md 内容（Use说明和指令）
- 参考文档列表（如有）

## Important

大多数外部技能（xlsx, docx, pptx, pdf 等）Yes**指令型技能**，没有预置脚本。
Read指令后应按照指令编写代码，Via `run_shell` Execute，而非Call `run_skill_script`。

## Related Skills

- `list-skills`: list所有技能
- `run-shell`: Execute按技能指令编写的代码
