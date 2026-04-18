"""
LSP tool definition

Modeled after CC LSPTool: provides code intelligence via Language Server Protocol,
including go-to-definition, find-references, symbol listing, and hover type info.
"""

LSP_TOOLS: list[dict] = [
    {
        "name": "lsp",
        "category": "System",
        "should_defer": True,
        "description": (
            "Code intelligence via Language Server Protocol. Provides go-to-definition, "
            "find-references, hover type info, document/workspace symbols, implementations, "
            "and call hierarchy. Requires a language server for the target language."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "goToDefinition",
                        "findReferences",
                        "hover",
                        "documentSymbol",
                        "workspaceSymbol",
                        "goToImplementation",
                        "prepareCallHierarchy",
                        "incomingCalls",
                        "outgoingCalls",
                    ],
                    "description": "The LSP operation to perform.",
                },
                "filePath": {
                    "type": "string",
                    "description": "Absolute path to the file.",
                },
                "line": {
                    "type": "integer",
                    "description": "Line number (1-based).",
                },
                "character": {
                    "type": "integer",
                    "description": "Character offset in line (1-based).",
                },
                "query": {
                    "type": "string",
                    "description": "Search query for workspaceSymbol operation.",
                },
            },
            "required": ["operation"],
        },
        "detail": (
            "Get code intelligence via Language Server Protocol.\n\n"
            "Supported operations:\n"
            "- goToDefinition: Jump to symbol definition\n"
            "- findReferences: Find all references\n"
            "- hover: Get type information and documentation\n"
            "- documentSymbol: List all symbols in a file\n"
            "- workspaceSymbol: Search symbols across the workspace\n"
            "- goToImplementation: Jump to interface implementations\n"
            "- prepareCallHierarchy: Prepare call hierarchy\n"
            "- incomingCalls: Find callers of this function\n"
            "- outgoingCalls: Find callees of this function\n\n"
            "Requires a language server for the target language to be available "
            "(e.g., pyright, typescript-language-server, gopls). File size limit 10MB."
        ),
        "triggers": [
            "Need to find where a symbol is defined",
            "Need to find all references to a function/class",
            "Need type information for a variable",
            "Need to list all symbols in a file",
            "Need call hierarchy analysis",
        ],
        "examples": [
            {
                "scenario": "Go to definition of a function",
                "params": {
                    "operation": "goToDefinition",
                    "filePath": "/path/to/file.py",
                    "line": 42,
                    "character": 10,
                },
                "expected": "File and line where the function is defined",
            },
            {
                "scenario": "Find all references to a class",
                "params": {
                    "operation": "findReferences",
                    "filePath": "/path/to/file.py",
                    "line": 5,
                    "character": 7,
                },
                "expected": "List of all files and lines referencing the class",
            },
        ],
    },
]
