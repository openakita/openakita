---
name: load-skill
description: Load a newly created skill from skills/ directory to make it immediately available
system: true
handler: skills
tool-name: load_skill
category: skills-management
---

# load-skill

Load新Create的技能到系统中，使其立即可用。

## Use Cases

- Use `skill-creator` Create技能后
- Manual在 `skills/` 目录Create技能后
- 需要立即Use新技能时

## Usage

```
load_skill(skill_name="my-new-skill")
```

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| skill_name | string | Yes | Skill name（即 skills/ 下的目录名）|

## Full工作流

1. Use `skill-creator` 技能Create SKILL.md
2. 将文件Save到 `skills/<skill-name>/SKILL.md`
3. Call `load_skill("<skill-name>")` Load skill
4. 技能立即可用

## Notes

- 技能目录必须Includes有效的 `SKILL.md` 文件
- 如果技能已存在，请Use `reload_skill` 重新Load
- Load成功后，技能会出现在 `list_skills` 列表中
