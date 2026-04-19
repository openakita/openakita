---
name: glob
description: Find files by glob pattern recursively. Results sorted by modification time (newest first). Auto-skips.git, node_modules and other common ignore directories.
system: true
handler: filesystem
tool-name: glob
category: File System
---

# Glob

search. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| pattern | string | Yes | Glob ( "*.py", "**/test_*.ts") |
| path | string | No | search (Default) |

## Examples

**Findhave Python **:
```json
{"pattern": "*.py"}
```

**Find**:
```json
{
 "pattern": "test_*.py",
 "path": "tests/"
}
```

**Find**:
```json
{"pattern": "*config*"}
```

## Notes

- not `**/` pattern willAutomatic `**/` search
- Automatic.git, node_modules, __pycache__
- ( in) 
- Returns

## Related Skills

- `grep`: search
- `list-directory`: list
- `read-file`: Read