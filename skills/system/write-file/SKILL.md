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

**Create配置文件**:
```json
{
  "path": "config.json",
  "content": "{\"debug\": true}"
}
```

**Write代码文件**:
```json
{
  "path": "hello.py",
  "content": "print('Hello, World!')"
}
```

## Notes

- 会覆盖已存在的文件
- AutomaticCreate父目录（如果不存在）
- Use UTF-8 编码

## Related Skills

- `read-file`: Read文件
- `run-shell`: Execute脚本
