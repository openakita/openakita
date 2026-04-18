---
name: reload-skill
description: Reload an existing skill to apply changes after modifying SKILL.md or scripts
system: true
handler: skills
tool-name: reload_skill
category: skills-management
---

# reload-skill

重新Load已存在的技能，以应用对 SKILL.md 或脚本的修改。

## Use Cases

- 修改了技能的 SKILL.md 后
- Update了技能的脚本后
- 需要刷新技能配置时

## Usage

```
reload_skill(skill_name="my-skill")
```

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| skill_name | string | Yes | Skill name |

## 工作原理

1. 卸载原有技能
2. 重新解析 SKILL.md
3. 重新注册到系统

## Notes

- 只能重新Load已Load过的技能
- 如果Yes新技能，请Use `load_skill`
- 重新Load后，技能的所有修改立即生效
