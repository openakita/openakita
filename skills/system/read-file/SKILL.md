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

**Read代码文件**:
```json
{"path": "src/main.py"}
```

## Notes

- 适Used for文本文件
- Use UTF-8 编码
- 二进制文件需要特殊处理

## Related Skills

- `write-file`: Write文件
- `list-directory`: List directory contents
