"""
MCP 工具定义

包含 MCP (Model Context Protocol) 相关的工具：
- call_mcp_tool: 调用 MCP 服务器工具
- list_mcp_servers: 列出 MCP 服务器
- get_mcp_instructions: 获取 MCP 使用说明
- add_mcp_server: 添加 MCP 服务器配置
- remove_mcp_server: 移除 MCP 服务器
- connect_mcp_server: 连接 MCP 服务器
- disconnect_mcp_server: 断开 MCP 服务器
- reload_mcp_servers: 重新加载所有 MCP 配置
"""

MCP_TOOLS = [
    {
        "name": "call_mcp_tool",
        "category": "MCP",
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
                "arguments": {"type": "object", "description": "工具参数", "default": {}},
            },
            "required": ["server", "tool_name"],
        },
    },
    {
        "name": "list_mcp_servers",
        "category": "MCP",
        "description": "List all configured MCP servers, their connection status, and available tool names with descriptions. When you need to: (1) Discover available MCP tools, (2) Check server connections.",
        "detail": """列出所有配置的 MCP 服务器及其完整工具清单。

**返回信息**：
- 服务器标识符和连接状态
- 每个服务器的工具名称和描述（已连接或预加载时）
- 未连接服务器的连接提示

**适用场景**：
- 查看可用的 MCP 服务器和工具
- 发现某个服务器提供的具体工具名
- 验证服务器连接状态""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_mcp_instructions",
        "category": "MCP",
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
            "properties": {"server": {"type": "string", "description": "服务器标识符"}},
            "required": ["server"],
        },
    },
    {
        "name": "add_mcp_server",
        "category": "MCP",
        "description": "Add/install a new MCP server configuration. Persists to workspace data/mcp/servers/ directory. When user asks to: (1) Install MCP server, (2) Add new tool integration, (3) Configure external MCP service.",
        "detail": """添加一个新的 MCP 服务器配置，持久化到工作区 data/mcp/servers/ 目录。

**传输协议**：
- stdio: 通过标准输入输出通信（需要 command），用于本地进程
- streamable_http: 通过 HTTP 通信（需要 url），用于远程服务
- sse: 通过 Server-Sent Events 通信（需要 url），兼容旧版 MCP 服务器

**示例**：
stdio 模式: add_mcp_server(name="web-search", transport="stdio", command="python", args=["-m", "my_mcp_server"])
HTTP 模式: add_mcp_server(name="remote-api", transport="streamable_http", url="http://localhost:8080/mcp")
SSE 模式: add_mcp_server(name="legacy-api", transport="sse", url="http://localhost:8080/sse")

**注意**：添加后会自动尝试连接并发现工具。如果连接失败，配置仍会保存，可稍后手动连接。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "服务器唯一标识符（如 web-search, my-database）"},
                "transport": {
                    "type": "string",
                    "enum": ["stdio", "streamable_http", "sse"],
                    "description": "传输协议: stdio(本地进程) | streamable_http(HTTP远程) | sse(SSE远程,兼容旧版MCP)",
                    "default": "stdio",
                },
                "command": {"type": "string", "description": "启动命令 (stdio 模式必填，如 python, npx, node)"},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "命令参数列表 (如 [\"-m\", \"my_server\"])",
                    "default": [],
                },
                "env": {
                    "type": "object",
                    "description": "额外环境变量 (如 {\"API_KEY\": \"xxx\"})",
                    "default": {},
                },
                "url": {"type": "string", "description": "服务 URL (streamable_http 模式必填)"},
                "description": {"type": "string", "description": "服务器描述 (可选)"},
                "instructions": {"type": "string", "description": "使用说明文本 (可选，将写入 INSTRUCTIONS.md)"},
                "auto_connect": {"type": "boolean", "description": "启动时是否自动连接此服务器 (默认 false)", "default": False},
            },
            "required": ["name"],
        },
    },
    {
        "name": "remove_mcp_server",
        "category": "MCP",
        "description": "Remove an MCP server configuration. Only removes servers in the workspace directory (not built-in ones). When user asks to: (1) Uninstall MCP server, (2) Remove tool integration.",
        "detail": """移除一个 MCP 服务器配置。

**注意**：
- 只能移除工作区 data/mcp/servers/ 中的配置
- 内置 mcps/ 中的配置不可移除
- 如果服务器已连接，会先自动断开""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要移除的服务器标识符"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "connect_mcp_server",
        "category": "MCP",
        "description": "Connect to a configured MCP server. Auto-discovers tools after connection. When you need to: (1) Activate an MCP server, (2) Establish connection before calling tools.",
        "detail": """连接到一个已配置的 MCP 服务器。

连接成功后会自动发现服务器上的工具、资源和提示词。
如果服务器已连接，直接返回成功。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "服务器标识符"},
            },
            "required": ["server"],
        },
    },
    {
        "name": "disconnect_mcp_server",
        "category": "MCP",
        "description": "Disconnect from a connected MCP server. When you need to: (1) Release server resources, (2) Troubleshoot connection issues by reconnecting.",
        "detail": """断开一个已连接的 MCP 服务器。

断开后该服务器的工具将不可用，直到重新连接。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "服务器标识符"},
            },
            "required": ["server"],
        },
    },
    {
        "name": "reload_mcp_servers",
        "category": "MCP",
        "description": "Reload all MCP server configurations from disk. Disconnects existing connections and rescans config directories. When you need to: (1) Pick up newly added configs, (2) Fix configuration issues.",
        "detail": """重新加载所有 MCP 服务器配置。

流程：
1. 断开所有已连接的服务器
2. 清空配置缓存
3. 重新扫描内置 mcps/ 和工作区 data/mcp/servers/ 目录
4. 重新注册到 MCPClient

**适用场景**：
- 手动修改了 MCP 配置文件后
- 需要刷新服务器列表""",
        "input_schema": {"type": "object", "properties": {}},
    },
]
