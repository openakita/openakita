---
name: edit-file
description: Edit file by exact string replacement. Finds old_string and replaces with new_string. Safer and more token-efficient than write_file for modifying existing files. Auto-handles Windows CRLF line endings.
system: true
handler: filesystem
tool-name: edit_file
category: File System
---

# Edit File

精确字符串替换式编辑文件。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| path | string | 是 | 文件路径 |
| old_string | string | 是 | 要替换的原文本（须精确匹配） |
| new_string | string | 是 | 替换后的新文本 |
| replace_all | boolean | 否 | 是否替换所有匹配项（默认 false） |

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

- 修改前请先用 read_file 确认文件内容
- old_string 必须精确匹配（包括缩进和空格）
- 如果 old_string 匹配多处且未设 replace_all，会报错
- 自动兼容 Windows CRLF 和 Unix LF 换行符
- 优先使用此工具而非 write_file 来编辑现有文件

## Related Skills

- `read-file`: 先读取文件确认内容
- `write-file`: create新文件或完全覆盖
- `grep`: search要编辑的内容位置
