---
name: get-skill-info
description: Get skill detailed instructions and usage guide (Level 2 disclosure). When you need to understand how to use a skill, check skill capabilities, or learn skill parameters.
system: true
handler: skills
tool-name: get_skill_info
category: Skills Management
---

# Get Skill Info

获取技能的详细信息和指令（Level 2 披露）。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| skill_name | string | 是 | 技能名称 |

## Returns

- 完整的 SKILL.md 内容（使用说明和指令）
- 参考文档列表（如有）

## Important

大多数外部技能（xlsx, docx, pptx, pdf 等）是**指令型技能**，没有预置脚本。
读取指令后应按照指令编写代码，通过 `run_shell` 执行，而非调用 `run_skill_script`。

## Related Skills

- `list-skills`: 列出所有技能
- `run-shell`: 执行按技能指令编写的代码

