---
name: glob
description: Find files by glob pattern recursively. Results sorted by modification time (newest first). Auto-skips .git, node_modules and other common ignore directories.
system: true
handler: filesystem
tool-name: glob
category: File System
---

# Glob

按文件名模式递归search文件。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| pattern | string | 是 | Glob 模式（如 "*.py"、"**/test_*.ts"） |
| path | string | 否 | search根目录（默认当前目录） |

## Examples

**查找所有 Python 文件**:
```json
{"pattern": "*.py"}
```

**查找测试文件**:
```json
{
  "pattern": "test_*.py",
  "path": "tests/"
}
```

**查找配置文件**:
```json
{"pattern": "*config*"}
```

## Notes

- 不以 `**/` 开头的 pattern 会自动加 `**/` 前缀进行递归search
- 自动跳过 .git、node_modules、__pycache__ 等目录
- 结果按修改时间降序排序（最新的在前）
- 返回相对路径列表

## Related Skills

- `grep`: 按内容search文件
- `list-directory`: list目录内容
- `read-file`: 读取找到的文件
