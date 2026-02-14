---
name: run-shell
description: Execute shell commands for system operations, directory creation, and script execution. When you need to run system commands, execute scripts, install packages, or manage processes. Note - if commands fail consecutively, try different approaches.
system: true
handler: filesystem
tool-name: run_shell
category: File System
---

# Run Shell

执行 Shell 命令。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| command | string | 是 | 要执行的 Shell 命令 |
| cwd | string | 否 | 工作目录（可选） |
| timeout | integer | 否 | 超时时间（秒），默认 60，范围 10-600 |

## Examples

**列出目录**:
```json
{"command": "ls -la"}
```

**安装依赖**:
```json
{"command": "pip install requests", "timeout": 300}
```

**在指定目录执行**:
```json
{"command": "npm install", "cwd": "/path/to/project"}
```

## Timeout Guidelines

- 简单命令: 30-60 秒
- 安装/下载: 300 秒
- 长时间任务: 根据需要设置更长时间

## Windows PowerShell 指引（重要）

### 转义保护

系统会自动将 PowerShell 命令通过 `-EncodedCommand`（Base64 UTF-16LE）编码执行，
避免 cmd.exe → PowerShell 的多层引号/特殊字符转义破坏。直接传入 PowerShell 命令即可。

### 何时使用 PowerShell vs Python 脚本

| 场景 | 推荐方式 | 原因 |
|------|----------|------|
| 简单系统查询（进程/服务/文件列表） | PowerShell cmdlet | `Get-Process`, `Get-ChildItem` 等一行搞定 |
| 复杂文本处理（正则、URL 提取、HTML/JSON 解析） | **Python 脚本** | 避免 PowerShell 正则 one-liner 的复杂性 |
| 批量文件操作（重命名、过滤、转换） | **Python 脚本** | 更可靠，不受 PowerShell 管道转义影响 |
| 网络下载/HTTP 请求 | **Python 脚本** | `requests`/`urllib` 比 `Invoke-WebRequest` 更灵活 |

### 推荐的 Python 脚本模式

对于复杂文本处理任务，**务必**使用 `write_file` + `run_shell` 组合：

```
步骤 1: write_file 写入 data/temp/task_xxx.py
步骤 2: run_shell "python data/temp/task_xxx.py"
```

**禁止**：在 `run_shell` 中写包含复杂正则的 PowerShell one-liner，例如：
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

- Windows 使用 PowerShell/cmd 命令（自动 EncodedCommand 编码）
- Linux/Mac 使用 bash 命令
- 如果命令连续失败，请尝试不同的命令或方法
- 失败时可调用 `get_session_logs` 查看详细日志

## Related Skills

- `write-file`: 写入文件
- `read-file`: 读取文件
