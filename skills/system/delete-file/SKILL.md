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

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | Yes | 要delete的文件或空Directory path |

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
- 非空目录会被拒绝，需Use run_shell Executedelete命令
- 路径受安全策略保护

## Related Skills

- `write-file`: create文件
- `list-directory`: View目录内容
- `run-shell`: 递归delete非空目录
