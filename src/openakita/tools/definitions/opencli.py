"""
OpenCLI 工具定义

将网站和 Electron 应用转化为 CLI 命令，复用 Chrome 登录态。
"""

from .base import build_detail

OPENCLI_TOOLS = [
    {
        "name": "opencli_list",
        "category": "Web",
        "description": (
            "List all available OpenCLI commands (website adapters and external CLIs). "
            "Use to discover what websites and tools can be operated via CLI. "
            "Returns structured command list with names and descriptions."
        ),
        "detail": build_detail(
            summary="列出 OpenCLI 可用的所有命令（网站 adapter 和外部 CLI）。",
            scenarios=[
                "发现可以通过 CLI 操作的网站和工具",
                "查看已安装的 opencli adapter",
            ],
            params_desc={
                "format": "输出格式：json（默认）或 yaml",
            },
        ),
        "triggers": [
            "When user asks what websites can be controlled via CLI",
            "When discovering available opencli commands before running one",
        ],
        "prerequisites": [],
        "warnings": [],
        "examples": [
            {
                "scenario": "列出可用命令",
                "params": {},
                "expected": "Returns list of available commands with descriptions",
            },
        ],
        "related_tools": [
            {"name": "opencli_run", "relation": "发现命令后执行"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["json", "yaml"],
                    "description": "输出格式（默认 json）",
                    "default": "json",
                },
            },
            "required": [],
        },
    },
    {
        "name": "opencli_run",
        "category": "Web",
        "description": (
            "Execute an OpenCLI command to interact with a website or tool. "
            "Commands are structured as '<site> <subcommand>' — e.g. 'github repos list', "
            "'bilibili video info', 'zhihu hot list'. "
            "Reuses the user's Chrome login session — no credentials needed. "
            "Returns structured JSON output. Much more reliable than browser_task for "
            "supported websites (Bilibili, GitHub, Twitter/X, YouTube, zhihu, etc.).\n\n"
            "PREFER this over browser_task when:\n"
            "- The target website has an opencli adapter (check with opencli_list)\n"
            "- The operation requires login state\n"
            "- You need deterministic, structured results"
        ),
        "detail": build_detail(
            summary="执行 OpenCLI 命令操作网站或工具。命令格式: '<site> <subcommand>'，复用 Chrome 登录态，返回结构化 JSON。",
            scenarios=[
                "操作需要登录的网站（如 GitHub、Bilibili、知乎）",
                "从网站提取结构化数据",
                "在 Electron 应用中执行操作",
            ],
            params_desc={
                "command": "要执行的命令（如 'zhihu hot list', 'bilibili video info', 'hackernews top'）",
                "args": "额外命令参数列表（可选）",
                "json_output": "是否请求 JSON 输出（默认 True）",
            },
            notes=[
                "先用 opencli_list 查看可用命令",
                "命令格式是 '<site> <subcommand>'，不需要加 'run' 前缀",
                "复用 Chrome 登录态，确保 Chrome 已打开并登录目标网站",
                "比 browser_task 更可靠，因为命令是确定性的",
            ],
        ),
        "triggers": [
            "When operating a website that has an opencli adapter",
            "When the task requires the user's login session on a website",
            "When browser_task is unreliable for the target website",
        ],
        "prerequisites": [
            "opencli must be installed (npm install -g @jackwener/opencli)",
            "Chrome must be running and logged into the target site",
        ],
        "warnings": [
            "Requires Chrome to be running with the Browser Bridge extension",
        ],
        "examples": [
            {
                "scenario": "查看知乎热榜",
                "params": {"command": "zhihu hot list"},
                "expected": "Returns JSON list of zhihu hot topics",
            },
            {
                "scenario": "查看 HackerNews 热门",
                "params": {"command": "hackernews top"},
                "expected": "Returns JSON list of top stories",
            },
            {
                "scenario": "获取 Bilibili 视频信息",
                "params": {"command": "bilibili video info", "args": ["BV1xx411c7XW"]},
                "expected": "Returns JSON with video metadata",
            },
        ],
        "related_tools": [
            {"name": "opencli_list", "relation": "先查看可用命令"},
            {"name": "opencli_doctor", "relation": "命令失败时诊断环境"},
            {"name": "browser_task", "relation": "无 adapter 时的降级方案"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的命令，格式: '<site> <subcommand>'（如 'zhihu hot list', 'hackernews top'）",
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
            "required": ["command"],
        },
    },
    {
        "name": "opencli_doctor",
        "category": "Web",
        "description": (
            "Diagnose OpenCLI environment: check Browser Bridge connectivity, "
            "Chrome extension status, and daemon health. Use when opencli commands fail."
        ),
        "detail": build_detail(
            summary="诊断 OpenCLI 环境，检查 Browser Bridge、Chrome 扩展和守护进程状态。",
            scenarios=[
                "opencli 命令执行失败时排查",
                "首次使用 opencli 前检查环境",
            ],
            params_desc={
                "live": "是否使用实时诊断模式（默认 False）",
            },
        ),
        "triggers": [
            "When opencli commands fail",
            "When setting up opencli for the first time",
        ],
        "prerequisites": [],
        "warnings": [],
        "examples": [
            {
                "scenario": "检查环境",
                "params": {},
                "expected": "Returns diagnostic information about opencli setup",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "live": {
                    "type": "boolean",
                    "description": "是否实时诊断",
                    "default": False,
                },
            },
            "required": [],
        },
    },
]
