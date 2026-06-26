"""
LSP 工具定义

参考 CC LSPTool：通过 Language Server Protocol 提供代码智能功能，
包括定义跳转、引用查找、符号列表、类型悬停等。
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
            "通过 Language Server Protocol 获取代码智能信息。\n\n"
            "支持的操作：\n"
            "- goToDefinition: 跳转到符号定义\n"
            "- findReferences: 查找所有引用\n"
            "- hover: 获取类型信息和文档\n"
            "- documentSymbol: 列出文件中的所有符号\n"
            "- workspaceSymbol: 在整个工作区搜索符号\n"
            "- goToImplementation: 跳转到接口实现\n"
            "- prepareCallHierarchy: 准备调用层次\n"
            "- incomingCalls: 查找调用此函数的地方\n"
            "- outgoingCalls: 查找此函数调用的地方\n\n"
            "需要目标语言的 LSP 服务器可用（如 pyright, typescript-language-server, "
            "gopls 等）。文件大小限制 10MB。"
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
