---
name: search-memory
description: Search relevant memories by keyword and optional type filter. When you need to recall past information, find user preferences, or check learned patterns.
system: true
handler: memory
tool-name: search_memory
category: Memory
---

# Search Memory

search.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| query | string | Yes | search |
| type | string | No | Filter by memory type() |

## Memory Types for Filter

- `fact`:
- `preference`:
- `skill`:
- `error`:
- `rule`:

## Examples

**search**:
```json
{"query": "", "type": "preference"}
```

**search**:
```json
{"query": "Python"}
```

## Related Skills

- `add-memory`:
- `get-memory-stats`: View
