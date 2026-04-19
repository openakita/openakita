---
name: list-directory
description: List directory contents including files and subdirectories. When you need to explore directory structure, find specific files, or check what exists in a folder.
system: true
handler: filesystem
tool-name: list_directory
category: File System
---

# List Directory

List directory contents.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | Yes | Directory path |

## Returns

- File name and type
- File size
- Modification time

## Examples

**List**:
```json
{"path": "."}
```

**List**:
```json
{"path": "/home/user/documents"}
```

## Related Skills

- `read-file`: Read file content
- `write-file`: Write
