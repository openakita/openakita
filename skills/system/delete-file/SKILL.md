---
name: delete-file
description: Delete a file or empty directory. Non-empty directories are rejected for safety. Use run_shell for recursive deletion.
system: true
handler: filesystem
tool-name: delete_file
category: File System
---

# Delete File

delete文件或空目录。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| path | string | 是 | 要delete的文件或空目录路径 |

## Examples

**delete文件**:
```json
{"path": "temp/output.txt"}
```

**delete空目录**:
```json
{"path": "temp/empty_dir"}
```

## Notes

- 仅delete文件或空目录
- 非空目录会被拒绝，需使用 run_shell 执行delete命令
- 路径受安全策略保护

## Related Skills

- `write-file`: create文件
- `list-directory`: 查看目录内容
- `run-shell`: 递归delete非空目录
