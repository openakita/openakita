---
name: list-directory
description: List directory contents including files and subdirectories. When you need to explore directory structure, find specific files, or check what exists in a folder.
system: true
handler: filesystem
tool-name: list_directory
category: File System
---

# List Directory

列出目录内容。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| path | string | 是 | 目录路径 |

## Returns

- 文件名和类型
- 文件大小
- 修改时间

## Examples

**列出当前目录**:
```json
{"path": "."}
```

**列出指定目录**:
```json
{"path": "/home/user/documents"}
```

## Related Skills

- `read-file`: 读取文件内容
- `write-file`: 写入文件
