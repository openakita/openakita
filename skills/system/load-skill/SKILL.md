---
name: load-skill
description: Load a newly created skill from skills/ directory to make it immediately available
system: true
handler: skills
tool-name: load_skill
category: skills-management
---

# load-skill

LoadCreate,. 

## Use Cases

- Use `skill-creator` Create
- Manualin `skills/` Create
- needUse

## Usage

```
load_skill(skill_name="my-new-skill")
```

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| skill_name | string | Yes | Skill name ( skills/ ) |

## Full

1. Use `skill-creator` Create SKILL.md
2. Save `skills/<skill-name>/SKILL.md`
3. Call `load_skill("<skill-name>")` Load skill
4.

## Notes

- Includeshave `SKILL.md`
- in, Use `reload_skill` Load
- Load, willin `list_skills`