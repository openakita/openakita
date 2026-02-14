---
name: write-file
description: Write content to file, creating new or overwriting existing. When you need to create new files, update file content, or save generated code/data.
system: true
handler: filesystem
tool-name: write_file
category: File System
---

# Write File

写入文件内容。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| path | string | 是 | 文件路径 |
| content | string | 是 | 文件内容 |

## Examples

**创建配置文件**:
```json
{
  "path": "config.json",
  "content": "{\"debug\": true}"
}
```

**写入代码文件**:
```json
{
  "path": "hello.py",
  "content": "print('Hello, World!')"
}
```

## Notes

- 会覆盖已存在的文件
- 自动创建父目录（如果不存在）
- 使用 UTF-8 编码

## Related Skills

- `read-file`: 读取文件
- `run-shell`: 执行脚本
