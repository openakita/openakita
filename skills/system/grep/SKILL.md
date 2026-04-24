---
name: grep
description: Search file contents using regex pattern across directories. Cross-platform pure Python implementation (no external tools needed). Returns matching lines with file paths and line numbers.
system: true
handler: filesystem
tool-name: grep
category: File System
---

# Grep

跨平台内容搜索工具。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| pattern | string | 是 | 正则表达式搜索模式 |
| path | string | 否 | 搜索目录（默认当前目录） |
| include | string | 否 | 文件名 glob 过滤（如 "*.py"） |
| context_lines | integer | 否 | 匹配行前后的上下文行数（默认 0） |
| max_results | integer | 否 | 最大返回匹配数（默认 50） |
| case_insensitive | boolean | 否 | 是否忽略大小写（默认 false） |

## Examples

**搜索函数定义**:
```json
{
  "pattern": "def test_",
  "include": "*.py"
}
```

**搜索 TODO 标记（忽略大小写）**:
```json
{
  "pattern": "TODO|FIXME",
  "case_insensitive": true,
  "max_results": 20
}
```

**搜索并显示上下文**:
```json
{
  "pattern": "class.*Error",
  "path": "src/",
  "context_lines": 3
}
```

## Notes

- 自动跳过 .git、node_modules、__pycache__、.venv 等目录
- 自动跳过二进制文件
- 纯 Python 实现，无需安装 ripgrep/grep
- 返回格式: file:line_number:content

## Related Skills

- `glob`: 按文件名模式查找文件
- `read-file`: 读取搜索到的文件
- `edit-file`: 编辑搜索到的匹配

