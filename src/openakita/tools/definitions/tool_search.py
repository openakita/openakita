"""
ToolSearch tool definition

Modeled after CC ToolSearchTool: when the model is unsure which tool it needs,
it can search lazily-loaded tools via a natural-language query and retrieve
their full schemas.  Discovered tools are automatically promoted to full
visibility in subsequent requests.
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
            "Search available tools. Use this when you need a capability but "
            "no currently visible tool is suitable, or when you see "
            "'[use tool_search to see full params]' in a tool's description.\n\n"
            "Returns the full parameter schema for matching tools so you can "
            "call them correctly."
        ),
    },
]
