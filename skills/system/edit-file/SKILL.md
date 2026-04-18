---
name: edit-file
description: Edit file by exact string replacement. Finds old_string and replaces with new_string. Safer and more token-efficient than write_file for modifying existing files. Auto-handles Windows CRLF line endings.
system: true
handler: filesystem
tool-name: edit_file
category: File System
---

# Edit File

精确字符串替换式Edit文件。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | Yes | File path |
| old_string | string | Yes | 要替换的原文本（须精确匹配） |
| new_string | string | Yes | 替换后的新文本 |
| replace_all | boolean | No | YesNo替换所有匹配项（Default false） |

## Examples

**修改函数名**:
```json
{
  "path": "src/main.py",
  "old_string": "def old_name():",
  "new_string": "def new_name():"
}
```

**批量替换变量名**:
```json
{
  "path": "src/config.py",
  "old_string": "old_var",
  "new_string": "new_var",
  "replace_all": true
}
```

## Notes

- 修改前请先用 read_file 确认File content
- old_string 必须精确匹配（包括缩进和空格）
- 如果 old_string 匹配多处且未设 replace_all，会报错
- Automatic兼容 Windows CRLF 和 Unix LF 换行符
- 优先Use此工具而非 write_file 来Edit现有文件

## Related Skills

- `read-file`: 先Read文件确认内容
- `write-file`: create新文件或完全覆盖
- `grep`: search要Edit的内容位置
