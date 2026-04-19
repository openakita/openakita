---
name: read-file
description: Read file content for text files. When you need to check file content, analyze code or data, or get configuration values.
system: true
handler: filesystem
tool-name: read_file
category: File System
---

# Read File

Read file content.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | Yes | File path |

## Examples

**Read config file**:
```json
{"path": "config.json"}
```

**Read**:
```json
{"path": "src/main.py"}
```

## Notes

- Used for
- Use UTF-8
- need

## Related Skills

- `write-file`: Write
- `list-directory`: List directory contents
