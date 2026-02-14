---
name: run-skill-script
description: Execute a skill's script file with arguments. When you need to run skill functionality, execute specific operations, or process data with skill.
system: true
handler: skills
tool-name: run_skill_script
category: Skills Management
---

# Run Skill Script

运行技能的脚本。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| skill_name | string | 是 | 技能名称 |
| script_name | string | 是 | 脚本文件名（如 get_time.py） |
| args | array | 否 | 命令行参数 |

## Workflow

1. 先用 `get_skill_info` 了解可用脚本
2. 指定脚本名称和参数执行

## Related Skills

- `get-skill-info`: 查看可用脚本
- `list-skills`: 列出所有技能
