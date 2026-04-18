"""
MCP tool definitions

Includes tools related to MCP (Model Context Protocol):
- call_mcp_tool: Call an MCP server tool
- list_mcp_servers: List MCP servers
- get_mcp_instructions: Get MCP usage instructions
- add_mcp_server: Add an MCP server configuration
- remove_mcp_server: Remove an MCP server
- connect_mcp_server: Connect to an MCP server
- disconnect_mcp_server: Disconnect from an MCP server
- reload_mcp_servers: Reload all MCP configurations
"""

MCP_TOOLS = [
    {
        "name": "call_mcp_tool",
        "category": "MCP",
        "description": "Call MCP server tool for extended capabilities. Check 'MCP Servers' section in system prompt for available servers and tools. When you need to: (1) Use external service, (2) Access specialized functionality.",
        "detail": """Call a tool on an MCP server.

**Before use**:
Check the 'MCP Servers' section in the system prompt for available servers and tools.

**When to use**:
- Using an external service
- Accessing specialized functionality

**Parameters**:
- server: MCP server identifier
- tool_name: tool name
- arguments: tool arguments""",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "MCP server identifier"},
                "tool_name": {"type": "string", "description": "Tool name"},
                "arguments": {"type": "object", "description": "Tool arguments", "default": {}},
            },
            "required": ["server", "tool_name"],
        },
    },
    {
        "name": "list_mcp_servers",
        "category": "MCP",
        "description": "List all configured MCP servers, their connection status, and available tool names with descriptions. When you need to: (1) Discover available MCP tools, (2) Check server connections.",
        "detail": """List all configured MCP servers along with their full tool inventory.

**Returns**:
- Server identifiers and connection status
- Each server's tool names and descriptions (when connected or preloaded)
- Connection hints for disconnected servers

**When to use**:
- View available MCP servers and tools
- Discover the specific tool names a server provides
- Verify server connection status""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_mcp_instructions",
        "category": "MCP",
        "description": "Get MCP server detailed usage instructions (INSTRUCTIONS.md). When you need to: (1) Understand server full capabilities, (2) Learn server-specific usage patterns.",
        "detail": """Get detailed usage instructions for an MCP server (INSTRUCTIONS.md).

**When to use**:
- Understand the server's full capabilities
- Learn server-specific usage patterns

**Returns**:
- Server capability description
- Tool usage guide
- Examples and best practices""",
        "input_schema": {
            "type": "object",
            "properties": {"server": {"type": "string", "description": "Server identifier"}},
            "required": ["server"],
        },
    },
    {
        "name": "add_mcp_server",
        "category": "MCP",
        "description": "Add/install a new MCP server configuration. Persists to workspace data/mcp/servers/ directory. When user asks to: (1) Install MCP server, (2) Add new tool integration, (3) Configure external MCP service.",
        "detail": """Add a new MCP server configuration, persisted to the workspace data/mcp/servers/ directory.

**Transport protocols**:
- stdio: communicates over standard I/O (requires command), used for local processes
- streamable_http: communicates over HTTP (requires url), used for remote services
- sse: communicates over Server-Sent Events (requires url), compatible with legacy MCP servers

**Examples**:
stdio mode: add_mcp_server(name="web-search", transport="stdio", command="python", args=["-m", "my_mcp_server"])
HTTP mode: add_mcp_server(name="remote-api", transport="streamable_http", url="http://localhost:8080/mcp")
SSE mode: add_mcp_server(name="legacy-api", transport="sse", url="http://localhost:8080/sse")

**Note**: after adding, it will automatically attempt to connect and discover tools. If the connection fails, the configuration is still saved and can be connected manually later.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique server identifier (e.g. web-search, my-database)",
                },
                "transport": {
                    "type": "string",
                    "enum": ["stdio", "streamable_http", "sse"],
                    "description": "Transport protocol: stdio (local process) | streamable_http (remote HTTP) | sse (remote SSE, legacy-MCP compatible)",
                    "default": "stdio",
                },
                "command": {
                    "type": "string",
                    "description": "Launch command (required for stdio mode, e.g. python, npx, node)",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Command argument list (e.g. ["-m", "my_server"])',
                    "default": [],
                },
                "env": {
                    "type": "object",
                    "description": 'Additional environment variables (e.g. {"API_KEY": "xxx"})',
                    "default": {},
                },
                "url": {"type": "string", "description": "Service URL (required for streamable_http mode)"},
                "headers": {
                    "type": "object",
                    "description": 'Custom HTTP headers (available for streamable_http/sse modes, e.g. {"Authorization": "Bearer xxx"})',
                    "default": {},
                },
                "description": {"type": "string", "description": "Server description (optional)"},
                "instructions": {
                    "type": "string",
                    "description": "Usage instructions text (optional, will be written to INSTRUCTIONS.md)",
                },
                "auto_connect": {
                    "type": "boolean",
                    "description": "Whether to auto-connect this server on startup (default false)",
                    "default": False,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "remove_mcp_server",
        "category": "MCP",
        "description": "Remove an MCP server configuration. Only removes servers in the workspace directory (not built-in ones). When user asks to: (1) Uninstall MCP server, (2) Remove tool integration.",
        "detail": """Remove an MCP server configuration.

**Note**:
- Only configurations under the workspace data/mcp/servers/ can be removed
- Built-in configurations under mcps/ cannot be removed
- If the server is connected, it will be disconnected first automatically""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Identifier of the server to remove"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "connect_mcp_server",
        "category": "MCP",
        "description": "Connect to a configured MCP server. Auto-discovers tools after connection. When you need to: (1) Activate an MCP server, (2) Establish connection before calling tools.",
        "detail": """Connect to a configured MCP server.

After a successful connection, tools, resources, and prompts on the server are auto-discovered.
If the server is already connected, returns success directly.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Server identifier"},
            },
            "required": ["server"],
        },
    },
    {
        "name": "disconnect_mcp_server",
        "category": "MCP",
        "description": "Disconnect from a connected MCP server. When you need to: (1) Release server resources, (2) Troubleshoot connection issues by reconnecting.",
        "detail": """Disconnect from a connected MCP server.

After disconnection, tools from that server are unavailable until reconnected.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Server identifier"},
            },
            "required": ["server"],
        },
    },
    {
        "name": "reload_mcp_servers",
        "category": "MCP",
        "description": "Reload all MCP server configurations from disk. Disconnects existing connections and rescans config directories. When you need to: (1) Pick up newly added configs, (2) Fix configuration issues.",
        "detail": """Reload all MCP server configurations.

Process:
1. Disconnect all connected servers
2. Clear the configuration cache
3. Rescan the built-in mcps/ and workspace data/mcp/servers/ directories
4. Re-register with MCPClient

**When to use**:
- After manually modifying MCP config files
- When the server list needs to be refreshed""",
        "input_schema": {"type": "object", "properties": {}},
    },
]
