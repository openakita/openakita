---
name: run-shell
description: Execute shell commands for system operations, directory creation, and script execution. When you need to run system commands, execute scripts, install packages, or manage processes. Note - if commands fail consecutively, try different approaches.
system: true
handler: filesystem
tool-name: run_shell
category: File System
---

# Run Shell

Execute shell commands.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| command | string | Yes | The Shell command to execute |
| cwd | string | No | Working directory (optional) |
| timeout | integer | No | Timeout duration (), Default 60, 10-600 |

## Examples

**List directory**:
```json
{"command": "ls -la"}
```

****:
```json
{"command": "pip install requests", "timeout": 300}
```

**inExecute**:
```json
{"command": "npm install", "cwd": "/path/to/project"}
```

## Timeout Guidelines

-: 30-60
- /Download: 300
-: needSet

## Windows PowerShell (need) 

###

willAutomatic PowerShell Via `-EncodedCommand` (Base64 UTF-16LE) Execute, 
cmd.exe → PowerShell /. PowerShell. 

### When to Use PowerShell vs Python

| | Recommendations | |
|------|----------|------|
| (//) | PowerShell cmdlet | `Get-Process`, `Get-ChildItem` |
| (, URL Extract, HTML/JSON ) | **Python ** | PowerShell one-liner |
| (,, ) | **Python ** |, not PowerShell |
| Download/HTTP | **Python ** | `requests`/`urllib` `Invoke-WebRequest` |

### Recommendations Python

, ****Use `write_file` + `run_shell`: 

```
1: write_file Write data/temp/task_xxx.py
2: run_shell "python data/temp/task_xxx.py"
```

****: in `run_shell` Includes PowerShell one-liner,: 
```
# this
powershell -Command "Get-Content file.html | Select-String -Pattern '(?<=src=\")[^\"]+' | ForEach-Object { $_.Matches.Value } | Sort-Object -Unique | Out-File urls.txt"
```

Python: 
```python
import re
from pathlib import Path
html = Path("file.html").read_text(encoding="utf-8")
urls = sorted(set(re.findall(r'src="([^"]+)"', html)))
Path("urls.txt").write_text("\n".join(urls), encoding="utf-8")
```

## Notes

- Windows Use PowerShell/cmd (Automatic EncodedCommand ) 
- Linux/Mac Use bash
-, not or
- Call `get_session_logs` View

## Related Skills

- `write-file`: Write
- `read-file`: Read