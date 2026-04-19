"""
Search tool definitions

Contains semantic search related tools:
- semantic_search: Search file content by semantic meaning
"""

SEARCH_TOOLS = [
    {
        "name": "semantic_search",
        "category": "Search",
        "description": (
            "Search files by meaning, not exact text. Ask complete questions "
            "like 'Where is authentication handled?' or 'How do we process payments?'\n\n"
            "When to use semantic_search vs grep:\n"
            "- semantic_search: Find code by meaning ('Where do we validate user input?')\n"
            "- grep: Find exact text matches ('ValidationError', 'def process_payment')\n\n"
            "Search strategy:\n"
            "- Start broad (path='' searches whole workspace)\n"
            "- If results point to a directory, rerun with that path\n"
            "- Break large questions into smaller ones\n"
            "- For big files (>1000 lines), scope search to that specific file\n\n"
            "IMPORTANT:\n"
            "- Ask complete questions, not single keywords (use grep for keywords)\n"
            "- One question per call; split multi-part questions into separate parallel calls"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A complete question (e.g., 'Where is user authentication handled?')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file path to scope the search. Empty string searches the entire workspace",
                    "default": "",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (1-15, default 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
]
