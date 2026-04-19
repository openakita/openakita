---
name: install-skill
description: Install skill from URL or Git repository to local skills/ directory. When you need to add new skill from GitHub or install SKILL.md from URL. Supports Git repos and single SKILL.md files.
system: true
handler: skills
tool-name: install_skill
category: Skills Management
---

# Install Skill

URL or Git install skills/ .

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| source | string | Yes | Git URL or SKILL.md URL |
| name | string | No | Skill name(Optional,Automatic SKILL.md Extract) |
| subdir | string | No | Git in Directory path |
| extra_files | array | No | needDownload URL |

## Supported Sources

1. **Git ** ( https://github.com/user/repo)
- AutomaticFind SKILL.md
- SupportsDirectory path

2. ** SKILL.md URL**
- create(scripts/, references/, assets/)

## Related Skills

- `list-skills`: List alreadyinstall
- `find-skills`: search
