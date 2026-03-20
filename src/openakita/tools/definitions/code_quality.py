"""
Code Quality 工具定义

包含代码质量检查相关工具：
- read_lints: 读取 linter 诊断
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
                        "文件或目录路径列表。不填则检查整个工作区（慎用，可能返回大量预先存在的错误）"
                    ),
                },
            },
        },
    },
]
