---
name: get-skill-reference
description: Get skill reference documentation for additional guidance. When you need to get detailed technical docs, find examples, or understand advanced usage.
system: true
handler: skills
tool-name: get_skill_reference
category: Skills Management
---

# Get Skill Reference

获取技能的参考文档。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| skill_name | string | 是 | 技能名称 |
| ref_name | string | 否 | 参考文档名称，默认 REFERENCE.md |

## Related Skills

- `get-skill-info`: 获取主要说明
- `run-skill-script`: 运行脚本
