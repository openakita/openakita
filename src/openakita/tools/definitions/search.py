"""
Search 工具定义

包含语义搜索相关工具：
- semantic_search: 按语义搜索文件内容
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
                    "description": "完整的问题（如 'Where is user authentication handled?'）",
                },
                "path": {
                    "type": "string",
                    "description": "搜索范围目录或文件路径。空字符串搜索整个工作区",
                    "default": "",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数（1-15，默认 10）",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
]
