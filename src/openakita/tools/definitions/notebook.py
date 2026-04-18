"""
Notebook tool definitions

Contains the Jupyter Notebook editing tool:
- edit_notebook: edit a notebook cell
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
                    "description": "Path to the notebook file (.ipynb)",
                },
                "cell_idx": {
                    "type": "integer",
                    "description": "Cell index (0-based)",
                },
                "is_new_cell": {
                    "type": "boolean",
                    "description": "true = create a new cell, false = edit an existing cell",
                },
                "cell_language": {
                    "type": "string",
                    "enum": [
                        "python",
                        "markdown",
                        "javascript",
                        "typescript",
                        "r",
                        "sql",
                        "shell",
                        "raw",
                        "other",
                    ],
                    "description": "Cell language type",
                },
                "old_string": {
                    "type": "string",
                    "description": (
                        "Text to replace (required when editing an existing cell; must uniquely match, include 3-5 lines of context)"
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text, or content for a new cell",
                },
            },
            "required": ["path", "cell_idx", "is_new_cell", "cell_language", "new_string"],
        },
    },
]
