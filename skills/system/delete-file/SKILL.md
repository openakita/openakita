---
name: delete-file
description: Delete a file or empty directory. Non-empty directories are rejected for safety. Use run_shell for recursive deletion.
system: true
handler: filesystem
tool-name: delete_file
category: File System
---

# Delete File

deleteor.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | Yes | needdelete orDirectory path |

## Examples

**delete**:
```json
{"path": "temp/output.txt"}
```

**delete**:
```json
{"path": "temp/empty_dir"}
```

## Notes

- deleteor
- will, Use run_shell Executedelete
-

## Related Skills

- `write-file`: create
- `list-directory`: View
- `run-shell`: delete
