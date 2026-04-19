---
name: write-file
description: Write content to file, creating new or overwriting existing. When you need to create new files, update file content, or save generated code/data.
system: true
handler: filesystem
tool-name: write_file
category: File System
---

# Write File

Write content to a file.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | Yes | File path |
| content | string | Yes | File content |

## Examples

**Create**:
```json
{
  "path": "config.json",
  "content": "{\"debug\": true}"
}
```

**Write**:
```json
{
  "path": "hello.py",
  "content": "print('Hello, World!')"
}
```

## Notes

- willin
- AutomaticCreate(notin)
- Use UTF-8

## Related Skills

- `read-file`: Read
- `run-shell`: Execute
