---
name: run-skill-script
description: Execute a skill's pre-built script file. When you need to run skill functionality, execute specific operations, or process data with skill scripts.
system: true
handler: skills
tool-name: run_skill_script
category: Skills Management
---

# Run Skill Script

Run a skill's pre-built script files.

## Important Note

Many skills (xlsx, docx, pptx, pdf, etc.) are **instruction-only** — they do NOT have pre-built scripts. If `run_skill_script` reports "Script not found" or "no executable scripts", do NOT retry. Instead:
1. Use `get_skill_info` to read the skill's instructions
2. Write code based on the instructions
3. Execute via `run_shell`

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| skill_name | string | Yes | Skill name |
| script_name | string | Yes | Script file name (e.g. `get_time.py`) |
| args | array | No | Command-line arguments |
| cwd | string | No | Working directory for script execution |

## Workflow

1. Check the skill's available scripts using `get_skill_info`
2. If scripts are available, proceed with execution
3. Pass `cwd` when processing user files (e.g. spreadsheet data)

## How to Check Available Scripts

Use `get_skill_info(skill_name)` to see the list of available scripts before running.

## Related Skills

- `get-skill-info`: View skill details and available scripts
- `list-skills`: List all installed skills
