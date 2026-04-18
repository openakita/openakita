"""
OpenCLI tool definitions.

Convert websites and Electron apps into CLI commands, reusing Chrome login sessions.
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
            summary="List all available OpenCLI commands (website adapters and external CLIs).",
            scenarios=[
                "Discover websites and tools that can be operated via CLI",
                "View installed opencli adapters",
            ],
            params_desc={
                "format": "Output format: json (default) or yaml",
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
                "scenario": "List available commands",
                "params": {},
                "expected": "Returns list of available commands with descriptions",
            },
        ],
        "related_tools": [
            {"name": "opencli_run", "relation": "Execute after discovering commands"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["json", "yaml"],
                    "description": "Output format (default: json)",
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
            summary="Execute an OpenCLI command to operate a website or tool. Command format: '<site> <subcommand>'. Reuses Chrome login session, returns structured JSON.",
            scenarios=[
                "Operate websites that require login (e.g., GitHub, Bilibili, Zhihu)",
                "Extract structured data from websites",
                "Perform actions in Electron apps",
            ],
            params_desc={
                "command": "Command to execute (e.g., 'zhihu hot list', 'bilibili video info', 'hackernews top')",
                "args": "Additional command arguments (optional)",
                "json_output": "Whether to request JSON output (default: True)",
            },
            notes=[
                "Use opencli_list first to check available commands",
                "Command format is '<site> <subcommand>', no 'run' prefix needed",
                "Reuses Chrome login session — make sure Chrome is open and logged into the target site",
                "More reliable than browser_task because commands are deterministic",
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
                "scenario": "View Zhihu hot topics",
                "params": {"command": "zhihu hot list"},
                "expected": "Returns JSON list of zhihu hot topics",
            },
            {
                "scenario": "View HackerNews top stories",
                "params": {"command": "hackernews top"},
                "expected": "Returns JSON list of top stories",
            },
            {
                "scenario": "Get Bilibili video info",
                "params": {"command": "bilibili video info", "args": ["BV1xx411c7XW"]},
                "expected": "Returns JSON with video metadata",
            },
        ],
        "related_tools": [
            {"name": "opencli_list", "relation": "Check available commands first"},
            {"name": "opencli_doctor", "relation": "Diagnose environment when commands fail"},
            {"name": "browser_task", "relation": "Fallback when no adapter is available"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command to execute, format: '<site> <subcommand>' (e.g., 'zhihu hot list', 'hackernews top')",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command arguments list",
                    "default": [],
                },
                "json_output": {
                    "type": "boolean",
                    "description": "Whether to request JSON output (default: True)",
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
            summary="Diagnose OpenCLI environment: check Browser Bridge, Chrome extension, and daemon status.",
            scenarios=[
                "Troubleshoot when opencli commands fail",
                "Check environment before first use of opencli",
            ],
            params_desc={
                "live": "Whether to use live diagnostic mode (default: False)",
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
                "scenario": "Check environment",
                "params": {},
                "expected": "Returns diagnostic information about opencli setup",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "live": {
                    "type": "boolean",
                    "description": "Whether to use live diagnostics",
                    "default": False,
                },
            },
            "required": [],
        },
    },
]
