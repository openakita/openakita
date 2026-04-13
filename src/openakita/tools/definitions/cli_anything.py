"""
CLI-Anything 工具定义

通过 CLI-Anything 生成的 CLI 接口控制桌面软件（GIMP、Blender、LibreOffice 等）。
"""

from .base import build_detail

CLI_ANYTHING_TOOLS = [
    {
        "name": "cli_anything_discover",
        "category": "Desktop",
        "description": (
            "Discover installed CLI-Anything tools on the system. "
            "Scans PATH for cli-anything-* commands (e.g. cli-anything-gimp, "
            "cli-anything-blender). Use to find what desktop software can be "
            "controlled via CLI."
        ),
        "detail": build_detail(
            summary="扫描系统 PATH，发现已安装的 cli-anything 桌面软件 CLI 工具。",
            scenarios=[
                "查看哪些桌面软件可以通过 CLI 控制",
                "首次使用前发现可用工具",
            ],
            params_desc={
                "refresh": "是否刷新缓存（默认 False）",
            },
        ),
        "triggers": [
            "When user asks to control desktop software like GIMP, Blender, LibreOffice",
            "When discovering available cli-anything tools",
        ],
        "prerequisites": [],
        "warnings": [],
        "examples": [
            {
                "scenario": "发现已安装工具",
                "params": {},
                "expected": "Returns list of installed cli-anything-* tools",
            },
        ],
        "related_tools": [
            {"name": "cli_anything_help", "relation": "发现工具后查看帮助"},
            {"name": "cli_anything_run", "relation": "发现工具后执行命令"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "description": "是否刷新缓存",
                    "default": False,
                },
            },
            "required": [],
        },
    },
    {
        "name": "cli_anything_run",
        "category": "Desktop",
        "description": (
            "Run a CLI-Anything command to control desktop software. "
            "Calls the real application backend — GIMP renders images, Blender renders 3D, "
            "LibreOffice generates documents. Returns structured JSON output.\n\n"
            "PREFER this over desktop_* tools when the target application has a "
            "cli-anything harness installed. Much more reliable than GUI automation."
        ),
        "detail": build_detail(
            summary="执行 cli-anything 命令控制桌面软件。直接调用软件后端 API，比 GUI 自动化可靠。",
            scenarios=[
                "用 GIMP 处理图片",
                "用 Blender 渲染 3D 场景",
                "用 LibreOffice 生成文档或 PDF",
                "用 Audacity 处理音频",
            ],
            params_desc={
                "app": "软件名称（如 'gimp', 'blender', 'libreoffice'）",
                "subcommand": "子命令（如 'image resize', 'render scene'）",
                "args": "命令参数列表",
                "json_output": "是否请求 JSON 输出（默认 True）",
            },
            notes=[
                "先用 cli_anything_discover 查看已安装的工具",
                "先用 cli_anything_help 查看可用子命令和参数",
                "目标软件必须安装在系统上",
                "生成的文件保存在服务器本地，IM 场景下需通过 `deliver_artifacts` 交付给用户",
            ],
        ),
        "triggers": [
            "When controlling desktop software through CLI",
            "When desktop_* GUI automation tools are unreliable",
            "When processing images, documents, 3D models, or audio via desktop apps",
        ],
        "prerequisites": [
            "cli-anything-<app> must be installed",
            "Target application must be installed on the system",
        ],
        "warnings": [
            "Target software must be installed — CLI-Anything calls real backends",
        ],
        "examples": [
            {
                "scenario": "GIMP 调整图片大小",
                "params": {
                    "app": "gimp",
                    "subcommand": "image resize",
                    "args": ["--width", "800", "--height", "600", "input.png"],
                },
                "expected": "Image resized via GIMP backend",
            },
            {
                "scenario": "LibreOffice 导出 PDF",
                "params": {
                    "app": "libreoffice",
                    "subcommand": "document export-pdf",
                    "args": ["report.docx"],
                },
                "expected": "Document exported as PDF",
            },
        ],
        "related_tools": [
            {"name": "cli_anything_help", "relation": "执行前查看可用子命令"},
            {"name": "cli_anything_discover", "relation": "查看已安装工具"},
            {"name": "desktop_click", "relation": "无 CLI 时的降级 GUI 方案"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "软件名称（如 'gimp', 'blender', 'libreoffice'）",
                },
                "subcommand": {
                    "type": "string",
                    "description": "子命令（如 'image resize', 'document export-pdf'）",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "命令参数列表",
                    "default": [],
                },
                "json_output": {
                    "type": "boolean",
                    "description": "是否请求 JSON 输出（默认 True）",
                    "default": True,
                },
            },
            "required": ["app", "subcommand"],
        },
    },
    {
        "name": "cli_anything_help",
        "category": "Desktop",
        "description": (
            "Get help documentation for a CLI-Anything tool or its subcommand. "
            "Shows available commands, parameters, and usage examples. "
            "Always check help before running a command for the first time."
        ),
        "detail": build_detail(
            summary="获取 cli-anything 工具的帮助文档。",
            scenarios=[
                "首次使用某个工具前了解可用命令",
                "查看子命令的参数说明",
            ],
            params_desc={
                "app": "软件名称（如 'gimp', 'blender'）",
                "subcommand": "子命令（可选，不填则显示顶层帮助）",
            },
        ),
        "triggers": [
            "When using a cli-anything tool for the first time",
            "When checking available subcommands and parameters",
        ],
        "prerequisites": ["cli-anything-<app> must be installed"],
        "warnings": [],
        "examples": [
            {
                "scenario": "查看 GIMP CLI 帮助",
                "params": {"app": "gimp"},
                "expected": "Shows top-level commands for cli-anything-gimp",
            },
            {
                "scenario": "查看特定子命令帮助",
                "params": {"app": "gimp", "subcommand": "image resize"},
                "expected": "Shows parameters for the resize subcommand",
            },
        ],
        "related_tools": [
            {"name": "cli_anything_run", "relation": "了解参数后执行"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "软件名称",
                },
                "subcommand": {
                    "type": "string",
                    "description": "子命令（可选）",
                },
            },
            "required": ["app"],
        },
    },
]
