---
name: install-skill
description: Install skill from URL or Git repository to local skills/ directory. When you need to add new skill from GitHub or install SKILL.md from URL. Supports Git repos and single SKILL.md files.
system: true
handler: skills
tool-name: install_skill
category: Skills Management
---

# Install Skill

从 URL 或 Git 仓库安装技能到本地 skills/ 目录。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| source | string | 是 | Git 仓库 URL 或 SKILL.md 文件 URL |
| name | string | 否 | 技能名称（可选，自动从 SKILL.md 提取） |
| subdir | string | 否 | Git 仓库中技能所在的子目录路径 |
| extra_files | array | 否 | 额外需要下载的文件 URL 列表 |

## Supported Sources

1. **Git 仓库** (如 https://github.com/user/repo)
   - 自动克隆仓库并查找 SKILL.md
   - 支持指定子目录路径

2. **单个 SKILL.md 文件 URL**
   - 创建规范目录结构（scripts/, references/, assets/）

## Related Skills

- `list-skills`: 查看已安装技能
- `generate-skill`: 自动生成技能
