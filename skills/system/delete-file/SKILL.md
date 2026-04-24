---
name: delete-file
description: Delete a file or empty directory. Non-empty directories are rejected for safety. Use run_shell for recursive deletion.
system: true
handler: filesystem
tool-name: delete_file
category: File System
---

# Delete File

删除文件或空目录。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| path | string | 是 | 要删除的文件或空目录路径 |

## Examples

**删除文件**:
```json
{"path": "temp/output.txt"}
```

**删除空目录**:
```json
{"path": "temp/empty_dir"}
```

## Notes

- 仅删除文件或空目录
- 非空目录会被拒绝，需使用 run_shell 执行删除命令
- 路径受安全策略保护

## Related Skills

- `write-file`: 创建文件
- `list-directory`: 查看目录内容
- `run-shell`: 递归删除非空目录

