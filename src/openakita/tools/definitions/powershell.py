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
                "Execute a PowerShell command on Windows. Use this instead of run_shell "
                "when the task requires PowerShell-specific features: cmdlets (Verb-Noun), "
                ".NET types, COM objects, WMI/CIM queries, registry access, or Windows-"
                "specific system management.\n\n"
                "Commands are executed via -EncodedCommand (Base64 UTF-16LE) to avoid "
                "quoting and escaping issues. Output is forced to UTF-8.\n\n"
                "IMPORTANT: For simple file operations (ls, cat, mkdir, cp, mv, rm), "
                "prefer run_shell — it's faster and more portable."
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
                        "description": "Timeout in seconds (default: 60, max: 600).",
                        "default": 60,
                    },
                },
                "required": ["command"],
            },
            "detail": (
                "Windows PowerShell 专用执行工具。\n\n"
                "使用场景：\n"
                "- 需要 PowerShell cmdlet（如 Get-Process, Get-ChildItem -Recurse）\n"
                "- 需要 .NET 类型操作（如 [System.IO.File]::ReadAllText()）\n"
                "- 需要 WMI/CIM 查询（如 Get-CimInstance Win32_OperatingSystem）\n"
                "- 需要注册表操作（如 Get-ItemProperty HKLM:\\SOFTWARE\\...）\n"
                "- 需要 COM 对象（如 New-Object -ComObject Excel.Application）\n"
                "- 需要管道操作（如 Get-Process | Where-Object {$_.CPU -gt 100}）\n\n"
                "不要用这个工具做简单的文件操作，用 run_shell 更快。"
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
            "related_tools": [
                {
                    "name": "run_shell",
                    "relation": "Use for non-PowerShell commands (bash, cmd)",
                },
            ],
        },
    ]
