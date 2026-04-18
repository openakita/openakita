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

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| pattern | string | Yes | Glob 模式（如 "*.py"、"**/test_*.ts"） |
| path | string | No | search根目录（Default当前目录） |

## Examples

**Find所有 Python 文件**:
```json
{"pattern": "*.py"}
```

**Find测试文件**:
```json
{
  "pattern": "test_*.py",
  "path": "tests/"
}
```

**Find配置文件**:
```json
{"pattern": "*config*"}
```

## Notes

- 不以 `**/` 开头的 pattern 会Automatic加 `**/` 前缀进行递归search
- Automatic跳过 .git、node_modules、__pycache__ 等目录
- 结果按修改时间降序排序（最新的在前）
- Returns相对路径列表

## Related Skills

- `grep`: 按内容search文件
- `list-directory`: list目录内容
- `read-file`: Read找到的文件
