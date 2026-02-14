---
name: search-memory
description: Search relevant memories by keyword and optional type filter. When you need to recall past information, find user preferences, or check learned patterns.
system: true
handler: memory
tool-name: search_memory
category: Memory
---

# Search Memory

搜索相关记忆。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| query | string | 是 | 搜索关键词 |
| type | string | 否 | 记忆类型过滤（可选） |

## Memory Types for Filter

- `fact`: 事实信息
- `preference`: 用户偏好
- `skill`: 技能知识
- `error`: 错误教训
- `rule`: 规则约定

## Examples

**搜索用户偏好**:
```json
{"query": "代码风格", "type": "preference"}
```

**通用搜索**:
```json
{"query": "Python"}
```

## Related Skills

- `add-memory`: 添加新记忆
- `get-memory-stats`: 查看记忆统计
