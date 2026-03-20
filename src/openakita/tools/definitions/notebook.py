"""
Notebook 工具定义

包含 Jupyter Notebook 编辑工具：
- edit_notebook: 编辑 Notebook cell
"""

NOTEBOOK_TOOLS = [
    {
        "name": "edit_notebook",
        "category": "File System",
        "description": (
            "Edit a Jupyter notebook cell or create a new cell.\n\n"
            "For editing existing cells: set is_new_cell=false, provide old_string and new_string.\n"
            "For creating new cells: set is_new_cell=true, provide new_string only.\n\n"
            "IMPORTANT:\n"
            "- Cell indices are 0-based\n"
            "- old_string MUST uniquely identify the target — include 3-5 lines of context\n"
            "- One change per call; make separate calls for multiple changes\n"
            "- Prefer editing existing cells over creating new ones\n"
            "- This tool does NOT support cell deletion (clear content with empty "
            "new_string instead)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Notebook 文件路径（.ipynb）",
                },
                "cell_idx": {
                    "type": "integer",
                    "description": "Cell 索引（0-based）",
                },
                "is_new_cell": {
                    "type": "boolean",
                    "description": "true=创建新 cell，false=编辑现有 cell",
                },
                "cell_language": {
                    "type": "string",
                    "enum": [
                        "python", "markdown", "javascript", "typescript",
                        "r", "sql", "shell", "raw", "other",
                    ],
                    "description": "Cell 语言类型",
                },
                "old_string": {
                    "type": "string",
                    "description": (
                        "要替换的文本（编辑现有 cell 时必填，需唯一匹配，"
                        "包含 3-5 行上下文）"
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": "替换后的文本或新 cell 的内容",
                },
            },
            "required": ["path", "cell_idx", "is_new_cell", "cell_language", "new_string"],
        },
    },
]
