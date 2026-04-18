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
| cwd | string | No | Working directory (optional） |
| timeout | integer | No | Timeout duration（秒），Default 60，范围 10-600 |

## Examples

**List directory**:
```json
{"command": "ls -la"}
```

**安装依赖**:
```json
{"command": "pip install requests", "timeout": 300}
```

**在指定目录Execute**:
```json
{"command": "npm install", "cwd": "/path/to/project"}
```

## Timeout Guidelines

- 简单命令: 30-60 秒
- 安装/Download: 300 秒
- 长时间任务: 根据需要Set更长时间

## Windows PowerShell 指引（重要）

### 转义保护

系统会Automatic将 PowerShell 命令Via `-EncodedCommand`（Base64 UTF-16LE）编码Execute，
避免 cmd.exe → PowerShell 的多层引号/特殊字符转义破坏。直接传入 PowerShell 命令即可。

### When to Use PowerShell vs Python 脚本

| 场景 | Recommendations方式 | 原因 |
|------|----------|------|
| 简单系统查询（进程/服务/文件列表） | PowerShell cmdlet | `Get-Process`, `Get-ChildItem` 等一行搞定 |
| 复杂文本处理（正则、URL Extract、HTML/JSON 解析） | **Python 脚本** | 避免 PowerShell 正则 one-liner 的复杂性 |
| 批量文件操作（重命名、过滤、转换） | **Python 脚本** | 更可靠，不受 PowerShell 管道转义影响 |
| 网络Download/HTTP 请求 | **Python 脚本** | `requests`/`urllib` 比 `Invoke-WebRequest` 更灵活 |

### Recommendations的 Python 脚本模式

对于复杂文本处理任务，**务必**Use `write_file` + `run_shell` 组合：

```
步骤 1: write_file Write data/temp/task_xxx.py
步骤 2: run_shell "python data/temp/task_xxx.py"
```

**禁止**：在 `run_shell` 中写Includes复杂正则的 PowerShell one-liner，例如：
```
# 禁止这种写法
powershell -Command "Get-Content file.html | Select-String -Pattern '(?<=src=\")[^\"]+' | ForEach-Object { $_.Matches.Value } | Sort-Object -Unique | Out-File urls.txt"
```

应改为写 Python 脚本：
```python
import re
from pathlib import Path
html = Path("file.html").read_text(encoding="utf-8")
urls = sorted(set(re.findall(r'src="([^"]+)"', html)))
Path("urls.txt").write_text("\n".join(urls), encoding="utf-8")
```

## Notes

- Windows Use PowerShell/cmd 命令（Automatic EncodedCommand 编码）
- Linux/Mac Use bash 命令
- 如果命令连续失败，请尝试不同的命令或方法
- 失败时可Call `get_session_logs` View详细日志

## Related Skills

- `write-file`: Write文件
- `read-file`: Read文件
