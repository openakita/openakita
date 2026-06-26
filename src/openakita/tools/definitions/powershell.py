"""
PowerShell 工具定义

独立于 run_shell 的 PowerShell 专用工具，参考 CC PowerShellTool 设计：
- Windows 平台自动启用
- PS 版本感知（Desktop 5.1 vs Core 7+）语法指导
- 只读 cmdlet 识别
- EncodedCommand 沙箱执行
"""

import platform

_IS_WINDOWS = platform.system() == "Windows"

POWERSHELL_TOOLS: list[dict] = []

if _IS_WINDOWS:
    POWERSHELL_TOOLS = [
        {
            "name": "run_powershell",
            "category": "File System",
            "description": (
                "Execute commands on Windows via PowerShell. This is the primary command "
                "execution tool — use it for ALL shell operations including:\n"
                "- Running python, git, npm, pip, node, and other CLI tools\n"
                "- PowerShell cmdlets (Get-Process, Get-ChildItem, etc.)\n"
                "- .NET types, COM objects, WMI/CIM queries, registry access\n"
                "- File operations (ls, mkdir, cp, mv, rm)\n\n"
                "Commands are executed via -EncodedCommand (Base64 UTF-16LE) to avoid "
                "quoting and escaping issues. Output is forced to UTF-8.\n\n"
                "Paths with spaces are handled correctly (no need for manual quoting)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The PowerShell command to execute. Write pure PowerShell "
                            "syntax — do NOT wrap in 'powershell -Command'. "
                            "The system handles encoding automatically."
                        ),
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Working directory for the command (optional).",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 120, max: 600).",
                        "default": 120,
                    },
                },
                "required": ["command"],
            },
            "detail": (
                "Windows 通用命令执行工具（基于 PowerShell）。\n\n"
                "使用场景：\n"
                "- 运行 Python/Git/npm/pip 等 CLI 工具\n"
                "- PowerShell cmdlet（如 Get-Process, Get-ChildItem -Recurse）\n"
                "- .NET 类型操作（如 [System.IO.File]::ReadAllText()）\n"
                "- WMI/CIM 查询（如 Get-CimInstance Win32_OperatingSystem）\n"
                "- 注册表操作（如 Get-ItemProperty HKLM:\\SOFTWARE\\...）\n"
                "- COM 对象（如 New-Object -ComObject Excel.Application）\n"
                "- 管道操作（如 Get-Process | Where-Object {$_.CPU -gt 100}）"
            ),
            "triggers": [
                "User asks for Windows system information",
                "Need to query WMI/CIM data",
                "Need PowerShell-specific cmdlets",
                "Need to access Windows registry",
                "Need .NET type operations",
            ],
            "examples": [
                {
                    "scenario": "List running processes sorted by memory",
                    "params": {
                        "command": (
                            "Get-Process | Sort-Object WorkingSet64 -Descending "
                            "| Select-Object -First 10 Name, "
                            "@{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}}"
                        ),
                    },
                    "expected": "Top 10 processes by memory usage",
                },
                {
                    "scenario": "Get system info",
                    "params": {
                        "command": (
                            "Get-CimInstance Win32_OperatingSystem "
                            "| Select-Object Caption, Version, OSArchitecture, "
                            "TotalVisibleMemorySize"
                        ),
                    },
                    "expected": "OS version and memory info",
                },
            ],
            "related_tools": [],
        },
    ]
