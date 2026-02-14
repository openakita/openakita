---
name: load-skill
description: Load a newly created skill from skills/ directory to make it immediately available
system: true
handler: skills
tool-name: load_skill
category: skills-management
---

# load-skill

加载新创建的技能到系统中，使其立即可用。

## 使用场景

- 使用 `skill-creator` 创建技能后
- 手动在 `skills/` 目录创建技能后
- 需要立即使用新技能时

## 使用方法

```
load_skill(skill_name="my-new-skill")
```

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skill_name | string | 是 | 技能名称（即 skills/ 下的目录名）|

## 完整工作流

1. 使用 `skill-creator` 技能创建 SKILL.md
2. 将文件保存到 `skills/<skill-name>/SKILL.md`
3. 调用 `load_skill("<skill-name>")` 加载技能
4. 技能立即可用

## 注意事项

- 技能目录必须包含有效的 `SKILL.md` 文件
- 如果技能已存在，请使用 `reload_skill` 重新加载
- 加载成功后，技能会出现在 `list_skills` 列表中
