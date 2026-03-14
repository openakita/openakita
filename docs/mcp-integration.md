# MCP Integration

OpenAkita supports the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) for connecting to external services.

## What is MCP?

MCP (Model Context Protocol) is a standard protocol for connecting AI models to external tools and data sources. It enables:

- Browser automation
- Database access
- File system operations
- Custom tool integration

## Supported MCP Servers

| Server | Description |
|--------|-------------|
| Browser | Web page automation via Playwright |
| Database | SQL database access |
| File System | Advanced file operations |
| Custom | Your own MCP servers |

## Configuration

### Enable MCP

```bash
# In .env
MCP_ENABLED=true
```

## Using MCP Tools

### Browser Automation

```
You> Go to github.com and search for "python web scraper"
Agent> Using browser MCP...
[Opens browser, navigates, performs search]
Found 1,234 repositories...
```

## MCP Tool Reference

### Browser Tools

| Tool | Description |
|------|-------------|
| `browser_navigate` | Navigate to URL |
| `browser_click` | Click element |
| `browser_type` | Type text |
| `browser_screenshot` | Take screenshot |
| `browser_get_text` | Get page text |

### Database Tools

| Tool | Description |
|------|-------------|
| `db_query` | Execute SELECT query |
| `db_execute` | Execute INSERT/UPDATE/DELETE |
| `db_describe` | Describe table structure |
| `db_list_tables` | List all tables |

## Creating Custom MCP Servers

### Basic Server

```python
# mcp_server/my_server.py
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("my-server")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="my_tool",
            description="Does something useful",
            inputSchema={
                "type": "object",
                "properties": {
                    "input": {"type": "string"}
                }
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "my_tool":
        result = process(arguments["input"])
        return [TextContent(type="text", text=result)]

if __name__ == "__main__":
    server.run()
```

### Register with OpenAkita

```yaml
# config/mcp_servers.yaml
servers:
  my-server:
    command: python
    args: ["mcp_server/my_server.py"]
    env:
      MY_VAR: value
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        OpenAkita                               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                    MCP Bridge                          │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │  │
│  │  │   Browser   │  │  Database   │  │   Custom    │   │  │
│  │  │   Client    │  │   Client    │  │   Client    │   │  │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │  │
│  └─────────┼────────────────┼────────────────┼──────────┘  │
│            │                │                │              │
└────────────┼────────────────┼────────────────┼──────────────┘
             │                │                │
             ▼                ▼                ▼
      ┌──────────┐     ┌──────────┐     ┌──────────┐
      │ Browser  │     │ Database │     │  Custom  │
      │  MCP     │     │   MCP    │     │   MCP    │
      │ Server   │     │  Server  │     │  Server  │
      └──────────┘     └──────────┘     └──────────┘
```

## Best Practices

### Security

- Use read-only database users when possible
- Limit browser MCP to specific domains
- Validate all inputs before execution

### Performance

- Reuse MCP connections
- Cache frequently accessed data
- Set appropriate timeouts

### Error Handling

```python
try:
    result = await mcp_client.call_tool("db_query", {"sql": query})
except MCPError as e:
    logger.error(f"MCP error: {e}")
    return fallback_result
```

## Troubleshooting

### MCP Server Won't Start

```bash
# Check server can run standalone
python mcp_server/my_server.py

# Check logs
LOG_LEVEL=DEBUG openakita
```

### Connection Timeout

```bash
# Increase timeout
MCP_TIMEOUT=60

# Check network connectivity
ping mcp-server-host
```

### Tool Not Found

1. Verify server is registered in config
2. Check tool is listed by server
3. Verify tool name matches exactly

## Resources

- [MCP Specification](https://modelcontextprotocol.io/docs)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP Examples](https://github.com/modelcontextprotocol/examples)
