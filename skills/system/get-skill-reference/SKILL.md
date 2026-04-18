---
name: get-skill-reference
description: Get skill reference documentation for additional guidance. When you need to get detailed technical docs, find examples, or understand advanced usage.
system: true
handler: skills
tool-name: get_skill_reference
category: Skills Management
---

# Get Skill Reference

get技能的参考文档。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| skill_name | string | Yes | Skill name |
| ref_name | string | No | 参考文档名称，Default REFERENCE.md |

## Related Skills

- `get-skill-info`: get主要说明
- `run-shell`: Execute按技能指令编写的代码
