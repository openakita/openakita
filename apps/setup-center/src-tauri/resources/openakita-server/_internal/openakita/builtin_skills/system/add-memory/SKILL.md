---
name: add-memory
description: Record important information to long-term memory for learning user preferences, successful patterns, and error lessons. When you need to remember user preferences, save successful patterns, or record lessons from errors.
system: true
handler: memory
tool-name: add_memory
category: Memory
---

# Add Memory

记录重要信息到长期记忆。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| content | string | 是 | 要记住的内容 |
| type | string | 是 | 记忆类型（见下方列表） |
| importance | number | 否 | 重要性（0-1），默认 0.5 |

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
  "content": "在 Windows 上使用 / 而不是 \\ 作为路径分隔符",
  "type": "error",
  "importance": 0.7
}
```

## Related Skills

- `search-memory`: 搜索相关记忆
- `get-memory-stats`: 查看记忆统计
