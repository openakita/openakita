---
name: search-memory
description: Search relevant memories by keyword and optional type filter. When you need to recall past information, find user preferences, or check learned patterns.
system: true
handler: memory
tool-name: search_memory
category: Memory
---

# Search Memory

search相关记忆。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| query | string | Yes | search关键词 |
| type | string | No | Filter by memory type（可选） |

## Memory Types for Filter

- `fact`: 事实信息
- `preference`: 用户偏好
- `skill`: 技能知识
- `error`: 错误教训
- `rule`: 规则约定

## Examples

**search用户偏好**:
```json
{"query": "代码风格", "type": "preference"}
```

**通用search**:
```json
{"query": "Python"}
```

## Related Skills

- `add-memory`: 添加新记忆
- `get-memory-stats`: View记忆统计
