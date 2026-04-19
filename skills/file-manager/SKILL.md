---
name: openakita/skills@file-manager
description: File and directory management tool for creating, reading, writing, deleting, moving, copying, and searching files. Use this skill when the user needs to perform file operations, list directories, search by pattern or content, or get file metadata like size and modification time.
license: MIT
metadata:
 author: openakita
 version: "1.0.0"
---

# File Manager

manageand. 

## When to Use

- create, delete,, or
- ReadorWrite file content
- search
- list
- get (, ) 

## Instructions

### list

```bash
python scripts/file_ops.py list <path> [--recursive] [--pattern "*.py"]
```

### Read

```bash
python scripts/file_ops.py read <file_path> [--encoding utf-8]
```

### Write

```bash
python scripts/file_ops.py write <file_path> --content "" [--append]
```

###

```bash
python scripts/file_ops.py copy <source> <destination>
```

### /

```bash
python scripts/file_ops.py move <source> <destination>
```

### delete

```bash
python scripts/file_ops.py delete <path> [--recursive]
```

### search

```bash
python scripts/file_ops.py search <directory> --pattern "*.py" [--content "search_text"]
```

### get

```bash
python scripts/file_ops.py info <path>
```

## Output Format

haveReturns JSON:

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

- deletenotResume, Use
- Writewillhave (Use --append) 
- need,