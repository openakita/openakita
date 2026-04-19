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
| type | string | Yes | Memory type (see list below) |
| importance | number | No | Importance (0-1), Default 0.5 |

## Memory Types

- `fact`:
- `preference`:
- `skill`:
- `error`:
- `rule`:

## Importance Levels

- 0.8+: (need, ) 
- 0.6-0.8: (, ) 
- 0.6-: () 

## Examples

****:
```json
{
"content": " ",
 "type": "preference",
 "importance": 0.8
}
```

****:
```json
{
"content": "in Windows Use / andnotYes \\ ",
 "type": "error",
 "importance": 0.7
}
```

## Related Skills

- `search-memory`: Search related memories
- `get-memory-stats`: View