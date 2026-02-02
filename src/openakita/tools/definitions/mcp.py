"""
MCP 工具定义

包含 MCP (Model Context Protocol) 相关的工具：
- call_mcp_tool: 调用 MCP 服务器工具
- list_mcp_servers: 列出 MCP 服务器
- get_mcp_instructions: 获取 MCP 使用说明
"""

MCP_TOOLS = [
    {
        "name": "call_mcp_tool",
        "description": "Call MCP server tool for extended capabilities. Check 'MCP Servers' section in system prompt for available servers and tools. When you need to: (1) Use external service, (2) Access specialized functionality.",
        "detail": """调用 MCP 服务器的工具。

**使用前**：
查看系统提示中的 'MCP Servers' 部分了解可用的服务器和工具。

**适用场景**：
- 使用外部服务
- 访问专用功能

**参数说明**：
- server: MCP 服务器标识符
- tool_name: 工具名称
- arguments: 工具参数""",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "MCP 服务器标识符"},
                "tool_name": {"type": "string", "description": "工具名称"},
                "arguments": {"type": "object", "description": "工具参数", "default": {}}
            },
            "required": ["server", "tool_name"]
        }
    },
    {
        "name": "list_mcp_servers",
        "description": "List all configured MCP servers and their connection status. When you need to: (1) Check available MCP servers, (2) Verify server connections.",
        "detail": """列出所有配置的 MCP 服务器及其连接状态。

**返回信息**：
- 服务器标识符
- 服务器名称
- 连接状态
- 可用工具数量

**适用场景**：
- 查看可用的 MCP 服务器
- 验证服务器连接""",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_mcp_instructions",
        "description": "Get MCP server detailed usage instructions (INSTRUCTIONS.md). When you need to: (1) Understand server full capabilities, (2) Learn server-specific usage patterns.",
        "detail": """获取 MCP 服务器的详细使用说明（INSTRUCTIONS.md）。

**适用场景**：
- 了解服务器的完整使用方法
- 学习服务器特定的使用模式

**返回内容**：
- 服务器功能说明
- 工具使用指南
- 示例和最佳实践""",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "服务器标识符"}
            },
            "required": ["server"]
        }
    },
]
