---
name: install-skill
description: Install skill from URL or Git repository to local skills/ directory. When you need to add new skill from GitHub or install SKILL.md from URL. Supports Git repos and single SKILL.md files.
system: true
handler: skills
tool-name: install_skill
category: Skills Management
---

# Install Skill

从 URL 或 Git 仓库install技能到本地 skills/ 目录。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| source | string | Yes | Git 仓库 URL 或 SKILL.md 文件 URL |
| name | string | No | Skill name（Optional,Automatic从 SKILL.md Extract） |
| subdir | string | No | Git 仓库中技能所在的子Directory path |
| extra_files | array | No | 额外需要Download的文件 URL 列表 |

## Supported Sources

1. **Git 仓库** (如 https://github.com/user/repo)
   - Automatic克隆仓库并Find SKILL.md
   - Supports指定子Directory path

2. **单个 SKILL.md 文件 URL**
   - create规范目录结构（scripts/, references/, assets/）

## Related Skills

- `list-skills`: List alreadyinstall技能
- `find-skills`: search可用技能
