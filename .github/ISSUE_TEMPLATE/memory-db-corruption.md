---
name: Memory database corruption
about: Backend fails to start with SQLite memory database errors
title: "[Memory DB] Backend fails to start"
labels: bug, data-integrity
assignees: ""
---

## Symptoms

Describe what happens when OpenAkita starts.

## Version

- OpenAkita version:
- Setup Center/Desktop version:
- Operating system:

## Log Evidence

Paste the last 200 lines of `openakita-serve.log`, especially any lines
containing `sqlite3.DatabaseError`, `malformed database schema`, `database disk
image is malformed`, or `not a database`.

```text
paste logs here
```

## Memory Directory Listing

Paste the output of:

```powershell
$MemoryDir = "D:\openakitadata\data\memory"
Get-ChildItem $MemoryDir -Force | Select-Object Name, Length, LastWriteTime
```

```text
paste listing here
```

## Recovery Attempted

Which recovery path did you try?

- [ ] Restore from backup
- [ ] sqlite3 `.recover`
- [ ] Recreate empty database
- [ ] Not attempted yet

## Notes

If you have a `.quarantine.<timestamp>` directory and it is safe to share, keep
it available for maintainers. Do not delete quarantined files until support
confirms they are no longer needed.
