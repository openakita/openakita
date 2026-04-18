---
name: add-memory
description: Record important information to long-term memory for learning user preferences, successful patterns, and error lessons. When you need to remember user preferences, save successful patterns, or record lessons from errors.
system: true
handler: memory
tool-name: add_memory
category: Memory
---

# Add Memory

Record important information to long-term memory.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| content | string | Yes | Content to remember |
| type | string | Yes | Memory type (see list below） |
| importance | number | No | Importance (0-1），Default 0.5 |

## Memory Types

- `fact`: 事实信息
- `preference`: 用户偏好
- `skill`: 技能知识
- `error`: 错误教训
- `rule`: 规则约定

## Importance Levels

- 0.8+: 永久记忆（重要偏好、关键规则）
- 0.6-0.8: 长期记忆（一般偏好、常用模式）
- 0.6-: 短期记忆（临时信息）

## Examples

**记录用户偏好**:
```json
{
  "content": "用户喜欢简洁的代码风格",
  "type": "preference",
  "importance": 0.8
}
```

**记录错误教训**:
```json
{
  "content": "在 Windows 上Use / 而不Yes \\ 作为路径分隔符",
  "type": "error",
  "importance": 0.7
}
```

## Related Skills

- `search-memory`: Search related memories
- `get-memory-stats`: View记忆统计
