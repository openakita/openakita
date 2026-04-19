"""
Code Quality tool definitions

Contains code quality check tools:
- read_lints: Read linter diagnostics
"""

CODE_QUALITY_TOOLS = [
    {
        "name": "read_lints",
        "category": "Code Quality",
        "description": (
            "Read linter/diagnostic errors for files or directories.\n\n"
            "Use after editing code files to check if you introduced any errors. "
            "Supports: Python (ruff/flake8/pylint), JavaScript/TypeScript (eslint), "
            "and other linters detected in the project.\n\n"
            "IMPORTANT:\n"
            "- NEVER call on files you haven't edited — it may return pre-existing errors\n"
            "- Prefer narrow scope (specific files) over wide scope (entire directory)\n"
            "- If you introduced errors, fix them before moving on\n"
            "- If errors were pre-existing, only fix them if necessary for your task"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of file or directory paths. Omit to check the entire workspace "
                        "(use with caution, may return many pre-existing errors)"
                    ),
                },
            },
        },
    },
]
