---
name: run-skill-script
description: Execute a skill's script file with arguments. When you need to run skill functionality, execute specific operations, or process data with skill.
system: true
handler: skills
tool-name: run_skill_script
category: Skills Management
---

# Run Skill Script

运行技能目录中的脚本。可复用工具必须是完整技能目录，而不是工作区根目录里的单个 `.py` 文件。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| skill_name | string | 是 | 技能名称 |
| script_name | string | 是 | 脚本文件名（如 get_time.py） |
| args | array | 否 | 命令行参数 |
| cwd | string | 否 | 工作目录；默认是技能目录 |

## Workflow

1. 先用 `get_skill_info` 了解可用脚本
2. 指定脚本名称和参数执行

## Skill Layout Rules

创建或修复可复用工具时，必须使用下面的结构：

```text
skills/<skill-id>/
  SKILL.md
  news_searcher.py
  scripts/
    main.py
```

- `script_name` 只能指向技能目录内的脚本，例如 `scripts/main.py` 或 `main.py`。
- 入口脚本导入的 Python 模块应放在同一个技能目录内。
- 不要把工作区根目录的 `news_searcher.py` 当作技能的一部分；那只是临时脚本，不会随 `run_skill_script` 稳定执行。
- 创建完目录和文件后，调用 `load_skill` 或 `reload_skill`，再用 `get_skill_info` 验证可执行脚本列表。

## Related Skills

- `get-skill-info`: 查看可用脚本
- `list-skills`: 列出所有技能

