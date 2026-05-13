# Memory DB Corruption Recovery

This guide helps recover a workspace when the backend log shows SQLite
corruption around `data/memory/openakita.db`, for example:

```text
sqlite3.DatabaseError: malformed database schema
```

Close OpenAkita before running any command below. Replace `$Workspace` with the
affected workspace/data directory if it is different.

## 1. Locate The Database

```powershell
$Workspace = "D:\openakitadata"
$MemoryDir = Join-Path $Workspace "data\memory"
$Db = Join-Path $MemoryDir "openakita.db"

Get-ChildItem $MemoryDir -Force | Select-Object Name, Length, LastWriteTime
```

## 2. Option A: Restore A Backup

Use this first when a recent `.bak` or `.snapshot` exists.

```powershell
$Backup = Get-ChildItem $MemoryDir -Force |
  Where-Object {
    $_.Name -like "openakita.db.bak.*" -or
    $_.Name -like "openakita.db.snapshot.*"
  } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

if (-not $Backup) {
  throw "No backup or snapshot found in $MemoryDir"
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Quarantine = Join-Path $MemoryDir ".quarantine.$Stamp"
New-Item -ItemType Directory -Path $Quarantine -Force | Out-Null

foreach ($Suffix in "", "-wal", "-shm") {
  $Path = "$Db$Suffix"
  if (Test-Path $Path) {
    Move-Item -Path $Path -Destination $Quarantine -Force
  }
}

Copy-Item -Path $Backup.FullName -Destination $Db -Force
Remove-Item "$Db-wal", "$Db-shm" -Force -ErrorAction SilentlyContinue

Write-Host "Restored $($Backup.Name). Original files were moved to $Quarantine"
```

Start OpenAkita again. If it still cannot start, repeat with an older backup or
try Option B.

## 3. Option B: Rebuild With sqlite3 `.recover`

Use this when no valid backup exists. The `.recover` command may salvage rows
from a damaged file, but it cannot guarantee complete recovery.

```powershell
$SqliteCandidates = @(
  "$env:LOCALAPPDATA\Programs\OpenAkita\sqlite3.exe",
  "$env:ProgramFiles\OpenAkita\sqlite3.exe",
  "sqlite3.exe"
)

$Sqlite = $SqliteCandidates | Where-Object {
  try { Get-Command $_ -ErrorAction Stop | Out-Null; $true } catch { Test-Path $_ }
} | Select-Object -First 1

if (-not $Sqlite) {
  throw "sqlite3.exe was not found. Install sqlite-tools or use Option C."
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RecoveredSql = Join-Path $MemoryDir "openakita.recovered.$Stamp.sql"
$RecoveredDb = Join-Path $MemoryDir "openakita.recovered.$Stamp.db"
$Quarantine = Join-Path $MemoryDir ".quarantine.$Stamp"

& $Sqlite $Db ".recover" | Out-File -FilePath $RecoveredSql -Encoding utf8
& $Sqlite $RecoveredDb ".read $RecoveredSql"
& $Sqlite $RecoveredDb "PRAGMA quick_check;"

New-Item -ItemType Directory -Path $Quarantine -Force | Out-Null
foreach ($Suffix in "", "-wal", "-shm") {
  $Path = "$Db$Suffix"
  if (Test-Path $Path) {
    Move-Item -Path $Path -Destination $Quarantine -Force
  }
}
Move-Item -Path $RecoveredDb -Destination $Db -Force
Remove-Item "$Db-wal", "$Db-shm" -Force -ErrorAction SilentlyContinue

Write-Host "Recovered database installed. Original files were moved to $Quarantine"
```

## 4. Option C: Recreate An Empty Memory Database

Use this only after the user accepts that long-term memory data may be lost. The
original files are quarantined first, so they can still be shared with support.

```powershell
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Quarantine = Join-Path $MemoryDir ".quarantine.$Stamp"
New-Item -ItemType Directory -Path $Quarantine -Force | Out-Null

foreach ($Suffix in "", "-wal", "-shm") {
  $Path = "$Db$Suffix"
  if (Test-Path $Path) {
    Move-Item -Path $Path -Destination $Quarantine -Force
  }
}

Write-Host "The damaged database was moved to $Quarantine"
Write-Host "Start OpenAkita again. It will create a fresh memory database."
```

## 5. What To Send Support

Attach these files when reporting the issue:

- The last 200 lines of `openakita-serve.log`.
- A directory listing of `data\memory`.
- The `.quarantine.<timestamp>` directory if it is small enough to share.

Do not delete quarantined files until support confirms they are no longer
needed.
