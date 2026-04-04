"""
ToolSearch 工具定义

参考 CC ToolSearchTool：模型不确定需要哪个工具时，通过自然语言查询
搜索延迟加载的工具，获取完整 schema。发现后的工具在后续请求中自动
提升为完整加载。
"""

TOOL_SEARCH_TOOLS: list[dict] = [
    {
        "name": "tool_search",
        "category": "System",
        "always_load": True,
        "description": (
            "Search for available tools by description. Use this when you need a "
            "capability but don't see the right tool, or when a tool shows "
            "'[use tool_search to see full params]' in its description.\n\n"
            "Returns the full parameter schema for matching tools so you can "
            "call them. Discovered tools are automatically promoted to full "
            "visibility in subsequent turns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language description of the capability you need. "
                        "Examples: 'schedule a task', 'take a screenshot', "
                        "'send message to telegram', 'edit jupyter notebook'"
                    ),
                },
            },
            "required": ["query"],
        },
        "detail": (
            "搜索可用工具。当你需要某个能力但当前可见工具中没有合适的，或者"
            "看到工具描述中标注 [use tool_search to see full params] 时，使用"
            "此工具搜索。\n\n"
            "返回匹配工具的完整参数 schema，使你可以正确调用它们。"
        ),
    },
]
