---
name: edit-file
description: Edit file by exact string replacement. Finds old_string and replaces with new_string. Safer and more token-efficient than write_file for modifying existing files. Auto-handles Windows CRLF line endings.
system: true
handler: filesystem
tool-name: edit_file
category: File System
---

# Edit File

Edit. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | Yes | File path |
| old_string | string | Yes | need () |
| new_string | string | Yes | |
| replace_all | boolean | No | YesNohave (Default false) |

## Examples

****:
```json
{
 "path": "src/main.py",
 "old_string": "def old_name():",
 "new_string": "def new_name():"
}
```

****:
```json
{
 "path": "src/config.py",
 "old_string": "old_var",
 "new_string": "new_var",
 "replace_all": true
}
```

## Notes

- read_file File content
- old_string (and) 
- old_string replace_all, will
- Automatic Windows CRLF and Unix LF
- Useand write_file Edithave

## Related Skills

- `read-file`: Read
- `write-file`: createor
- `grep`: searchneedEdit