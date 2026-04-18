---
name: run-skill-script
description: Execute a skill's script file with arguments. When you need to run skill functionality, execute specific operations, or process data with skill.
system: true
handler: skills
tool-name: run_skill_script
category: Skills Management
---

# Run Skill Script

Run技能的脚本。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| skill_name | string | Yes | Skill name |
| script_name | string | Yes | Script file name（如 get_time.py） |
| args | array | No | Command-line arguments |

## Workflow

1. 先用 `get_skill_info` 了解可用脚本
2. 指定脚本名称和参数Execute

## Related Skills

- `get-skill-info`: View可用脚本
- `list-skills`: list所有技能
