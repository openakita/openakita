---
name: grep
description: Search file contents using regex pattern across directories. Cross-platform pure Python implementation (no external tools needed). Returns matching lines with file paths and line numbers.
system: true
handler: filesystem
tool-name: grep
category: File System
---

# Grep

search. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| pattern | string | Yes | search |
| path | string | No | search (Default) |
| include | string | No | glob ( "*.py") |
| context_lines | integer | No | (Default 0) |
| max_results | integer | No | MaximumReturns (Default 50) |
| case_insensitive | boolean | No | YesNo (Default false) |

## Examples

**search**:
```json
{
 "pattern": "def test_",
 "include": "*.py"
}
```

**search TODO Mark () **:
```json
{
 "pattern": "TODO|FIXME",
 "case_insensitive": true,
 "max_results": 20
}
```

**searchDisplay**:
```json
{
 "pattern": "class.*Error",
 "path": "src/",
 "context_lines": 3
}
```

## Notes

- Automatic.git, node_modules, __pycache__,.venv
- Automatic
- Python, install ripgrep/grep
- Returns: file:line_number:content

## Related Skills

- `glob`: Find
- `read-file`: Readsearch
- `edit-file`: Editsearch