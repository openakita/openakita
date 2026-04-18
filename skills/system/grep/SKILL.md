---
name: grep
description: Search file contents using regex pattern across directories. Cross-platform pure Python implementation (no external tools needed). Returns matching lines with file paths and line numbers.
system: true
handler: filesystem
tool-name: grep
category: File System
---

# Grep

跨平台内容search工具。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| pattern | string | Yes | 正则表达式search模式 |
| path | string | No | search目录（Default当前目录） |
| include | string | No | 文件名 glob 过滤（如 "*.py"） |
| context_lines | integer | No | 匹配行前后的上下文行数（Default 0） |
| max_results | integer | No | MaximumReturns匹配数（Default 50） |
| case_insensitive | boolean | No | YesNo忽略大小写（Default false） |

## Examples

**search函数定义**:
```json
{
  "pattern": "def test_",
  "include": "*.py"
}
```

**search TODO Mark（忽略大小写）**:
```json
{
  "pattern": "TODO|FIXME",
  "case_insensitive": true,
  "max_results": 20
}
```

**search并Display上下文**:
```json
{
  "pattern": "class.*Error",
  "path": "src/",
  "context_lines": 3
}
```

## Notes

- Automatic跳过 .git、node_modules、__pycache__、.venv 等目录
- Automatic跳过二进制文件
- 纯 Python 实现，无需install ripgrep/grep
- Returns格式: file:line_number:content

## Related Skills

- `glob`: 按文件名模式Find文件
- `read-file`: Readsearch到的文件
- `edit-file`: Editsearch到的匹配
