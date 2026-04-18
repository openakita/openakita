---
name: openakita/skills@file-manager
description: File and directory management tool for creating, reading, writing, deleting, moving, copying, and searching files. Use this skill when the user needs to perform file operations, list directories, search by pattern or content, or get file metadata like size and modification time.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# File Manager

manage文件和目录的工具集。

## When to Use

- create、delete、移动、复制文件或目录
- 读取或写入文件内容
- search文件
- list目录内容
- get文件信息（大小、修改时间等）

## Instructions

### list目录

```bash
python scripts/file_ops.py list <path> [--recursive] [--pattern "*.py"]
```

### 读取文件

```bash
python scripts/file_ops.py read <file_path> [--encoding utf-8]
```

### 写入文件

```bash
python scripts/file_ops.py write <file_path> --content "内容" [--append]
```

### 复制文件

```bash
python scripts/file_ops.py copy <source> <destination>
```

### 移动/重命名

```bash
python scripts/file_ops.py move <source> <destination>
```

### delete

```bash
python scripts/file_ops.py delete <path> [--recursive]
```

### search文件

```bash
python scripts/file_ops.py search <directory> --pattern "*.py" [--content "search_text"]
```

### get文件信息

```bash
python scripts/file_ops.py info <path>
```

## Output Format

所有操作返回 JSON 格式:

```json
{
  "success": true,
  "operation": "list",
  "data": {
    "files": ["file1.py", "file2.py"],
    "directories": ["subdir"],
    "count": 3
  }
}
```

## Safety Notes

- delete操作不可恢复，谨慎使用
- 写入文件会覆盖原有内容（除非使用 --append）
- 对于重要文件，建议先备份
