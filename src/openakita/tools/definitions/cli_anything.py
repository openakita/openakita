"""
CLI-Anything tool definitions

Control desktop software (GIMP, Blender, LibreOffice, etc.) via CLI-Anything
generated CLI interfaces.
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
            summary="Scan system PATH to discover installed cli-anything desktop software CLI tools.",
            scenarios=[
                "Check which desktop software can be controlled via CLI",
                "Discover available tools before first use",
            ],
            params_desc={
                "refresh": "Whether to refresh the cache (default False)",
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
                "scenario": "Discover installed tools",
                "params": {},
                "expected": "Returns list of installed cli-anything-* tools",
            },
        ],
        "related_tools": [
            {"name": "cli_anything_help", "relation": "View help after discovering tools"},
            {"name": "cli_anything_run", "relation": "Run commands after discovering tools"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "description": "Whether to refresh the cache",
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
            summary="Execute cli-anything commands to control desktop software. Directly calls the application backend API, more reliable than GUI automation.",
            scenarios=[
                "Process images with GIMP",
                "Render 3D scenes with Blender",
                "Generate documents or PDFs with LibreOffice",
                "Process audio with Audacity",
            ],
            params_desc={
                "app": "Application name (e.g. 'gimp', 'blender', 'libreoffice')",
                "subcommand": "Subcommand (e.g. 'image resize', 'render scene')",
                "args": "Command argument list",
                "json_output": "Whether to request JSON output (default True)",
            },
            notes=[
                "Use cli_anything_discover first to check installed tools",
                "Use cli_anything_help first to check available subcommands and parameters",
                "Target software must be installed on the system",
                "Generated files are saved on the server locally; in IM scenarios, deliver to user via `deliver_artifacts`",
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
                "scenario": "Resize image with GIMP",
                "params": {
                    "app": "gimp",
                    "subcommand": "image resize",
                    "args": ["--width", "800", "--height", "600", "input.png"],
                },
                "expected": "Image resized via GIMP backend",
            },
            {
                "scenario": "Export PDF with LibreOffice",
                "params": {
                    "app": "libreoffice",
                    "subcommand": "document export-pdf",
                    "args": ["report.docx"],
                },
                "expected": "Document exported as PDF",
            },
        ],
        "related_tools": [
            {"name": "cli_anything_help", "relation": "View available subcommands before running"},
            {"name": "cli_anything_discover", "relation": "View installed tools"},
            {"name": "desktop_click", "relation": "Fallback GUI option when no CLI is available"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Application name (e.g. 'gimp', 'blender', 'libreoffice')",
                },
                "subcommand": {
                    "type": "string",
                    "description": "Subcommand (e.g. 'image resize', 'document export-pdf')",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command argument list",
                    "default": [],
                },
                "json_output": {
                    "type": "boolean",
                    "description": "Whether to request JSON output (default True)",
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
            summary="Get help documentation for a cli-anything tool.",
            scenarios=[
                "Learn available commands before using a tool for the first time",
                "View parameter descriptions for a subcommand",
            ],
            params_desc={
                "app": "Application name (e.g. 'gimp', 'blender')",
                "subcommand": "Subcommand (optional; if omitted, shows top-level help)",
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
                "scenario": "View GIMP CLI help",
                "params": {"app": "gimp"},
                "expected": "Shows top-level commands for cli-anything-gimp",
            },
            {
                "scenario": "View help for a specific subcommand",
                "params": {"app": "gimp", "subcommand": "image resize"},
                "expected": "Shows parameters for the resize subcommand",
            },
        ],
        "related_tools": [
            {"name": "cli_anything_run", "relation": "Run after reviewing parameters"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Application name",
                },
                "subcommand": {
                    "type": "string",
                    "description": "Subcommand (optional)",
                },
            },
            "required": ["app"],
        },
    },
]
