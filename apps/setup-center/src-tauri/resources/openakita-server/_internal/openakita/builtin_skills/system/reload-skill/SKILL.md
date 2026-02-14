---
name: reload-skill
description: Reload an existing skill to apply changes after modifying SKILL.md or scripts
system: true
handler: skills
tool-name: reload_skill
category: skills-management
---

# reload-skill

重新加载已存在的技能，以应用对 SKILL.md 或脚本的修改。

## 使用场景

- 修改了技能的 SKILL.md 后
- 更新了技能的脚本后
- 需要刷新技能配置时

## 使用方法

```
reload_skill(skill_name="my-skill")
```

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skill_name | string | 是 | 技能名称 |

## 工作原理

1. 卸载原有技能
2. 重新解析 SKILL.md
3. 重新注册到系统

## 注意事项

- 只能重新加载已加载过的技能
- 如果是新技能，请使用 `load_skill`
- 重新加载后，技能的所有修改立即生效
