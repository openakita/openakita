---
name: openakita/skills@mcp-installer
description: Install, configure, and add MCP servers to the OpenAkita system. Use when the user needs to install MCP packages (npm/pip/uvx), connect remote HTTP/SSE MCP services, set up custom local MCP servers, or manage MCP server configuration and lifecycle.
license: MIT
metadata:
 author: openakita
 version: "1.0.0"
---

# MCP Installer — and MCP

## System MCP

OpenAkita UseManage MCP. MCP Yes, Includesand: 

```
<server-name>/
├── SERVER_METADATA.json #: 
├── INSTRUCTIONS.md #: Use (Provides) 
└── tools/ #: (Automatic) 
 ├── tool1.json
 └── tool2.json
```

### Configuration

| | Description | |
|------|------|------|
| `mcps/` | MCP () | No |
| `.mcp/` | | No |
| `data/mcp/servers/` | /AI | **Yes** |

**have MCP Write `data/mcp/servers/`. **

###

| | | |
|------|------|---------|
| `stdio` | (npx/python/node) | `command` + `args` |
| `streamable_http` | HTTP | `url` |
| `sse` | MCP (SSE) | `url` |

---

## Installation

###: Use `add_mcp_server` (Recommendations) 

`add_mcp_server`, MCP: 

**stdio (npx ): **
```
add_mcp_server(
 name="filesystem",
 transport="stdio",
 command="npx",
 args=["-y", "@anthropic/mcp-server-filesystem", "/path/to/dir"],
description=""
)
```

**stdio (Python ): **
```
add_mcp_server(
 name="my-tool",
 transport="stdio",
 command="python",
 args=["-m", "my_mcp_package"],
description=" MCP ",
 env={"API_KEY": "xxx"}
)
```

**stdio (uvx ): **
```
add_mcp_server(
 name="my-tool",
 transport="stdio",
 command="uvx",
 args=["my-mcp-package"],
description=" MCP "
)
```

**streamable_http (): **
```
add_mcp_server(
 name="remote-api",
 transport="streamable_http",
 url="http://localhost:8080/mcp",
description=" API "
)
```

**sse (): **
```
add_mcp_server(
 name="legacy-api",
 transport="sse",
 url="http://localhost:8080/sse",
description=" SSE "
)
```

###: ManualCreate

in `data/mcp/servers/` Create. 

**: Create**
```bash
mkdir -p data/mcp/servers/<server-name>
```

**: Write SERVER_METADATA.json**

```json
{
 "serverIdentifier": "<server-name>",
"serverName": "Display",
"serverDescription": "",
 "command": "npx",
 "args": ["-y", "package-name"],
 "env": {},
 "transport": "stdio",
 "url": "",
 "autoConnect": false
}
```

** (): Create INSTRUCTIONS.md**

MCP Use, Agent inneedLoad. 

** (): **

in `tools/` Create JSON (): 

```json
{
 "name": "tool_name",
"description": "",
 "inputSchema": {
 "type": "object",
 "properties": {
 "param1": {
 "type": "string",
"description": ""
 }
 },
 "required": ["param1"]
 }
}
```

> Yes -- willAutomatic. Yesin Agent alsoin. 

**: Load**

ManualCreateCall `reload_mcp_servers` Load. 

---

## SERVER_METADATA.json Full

| | Type | | Description |
|------|------|------|------|
| `serverIdentifier` | string | Yes |, and |
| `serverName` | string | Yes | Display |
| `serverDescription` | string | No | |
| `command` | string | stdio | Launch (python/npx/node/uvx ) |
| `args` | string[] | No | |
| `env` | object | No | |
| `transport` | string | No |: `stdio` (Default) /`streamable_http`/`sse` |
| `url` | string | HTTP/SSE | URL |
| `autoConnect` | boolean | No | LaunchAutomatic (Default false) |

: `"type": "streamableHttp"` `"transport": "streamable_http"`. 

---

## MCP

### npm (Via npx) 

```
add_mcp_server(
 name="github",
 command="npx",
 args=["-y", "@modelcontextprotocol/server-github"],
 env={"GITHUB_PERSONAL_ACCESS_TOKEN": "<token>"},
 description="GitHub API"
)
```

```
add_mcp_server(
 name="puppeteer",
 command="npx",
 args=["-y", "@anthropic/mcp-server-puppeteer"],
description="Puppeteer Automatic"
)
```

```
add_mcp_server(
 name="sqlite",
 command="npx",
 args=["-y", "@anthropic/mcp-server-sqlite", "path/to/db.sqlite"],
description="SQLite "
)
```

### Python (Via python -m or uvx) 

```
add_mcp_server(
 name="arxiv",
 command="uvx",
 args=["mcp-server-arxiv"],
description="arXiv Search"
)
```

```
add_mcp_server(
 name="postgres",
 command="python",
 args=["-m", "mcp_server_postgres", "postgresql://user:pass@localhost/db"],
description="PostgreSQL "
)
```

### Remote HTTP

```
add_mcp_server(
 name="composio",
 transport="streamable_http",
 url="https://mcp.composio.dev/partner/mcp_xxxx",
description="Composio "
)
```

### LocalCreate MCP

Use `mcp-builder` Create MCP, ****inCreateCall `add_mcp_server`: 

**Python (Use): **
```
add_mcp_server(
 name="my-custom-tool",
 command="python",
 args=["C:/path/to/my_project/server.py"],
description=" MCP "
)
```

**Python: **
```
add_mcp_server(
 name="my-custom-tool",
 command="python",
 args=["-m", "my_mcp_project.server"],
description=" MCP "
)
```

**TypeScript (): **
```
add_mcp_server(
 name="my-custom-tool",
 command="node",
 args=["C:/path/to/my_project/dist/index.js"],
description=" MCP "
)
```

> **need**: Use****, Working directorynotand. 

---

## Installation

1. ****: stdio `command` YesNoin PATH (`which npx`, `which python`) 
2. ****: npm need Node.js, Python need
3. **/URL **: HTTP/SSE URL
4. ****: MCP need API Key, Via `env`
5. ****: `serverIdentifier` Useand ( `my-tool`), 

## Installation

willAutomatic. Automatic: 

1. Use `connect_mcp_server("server-name")` Manual
2. Use `list_mcp_servers` View
3. Use `call_mcp_tool("server-name", "tool_name", {...})` Call

##

| | | |
|------|---------|---------|
| | ornotin PATH | Run (Node.js/Python) |
| | Launchor | `MCP_CONNECT_TIMEOUT` (Default 30s) |
| HTTP | URL orLaunch | URL Run |
| | | `connect_mcp_server` |
| | API Key or | `env` |

## Manage

- **List**: `list_mcp_servers`
- ****: `connect_mcp_server("name")`
- ****: `disconnect_mcp_server("name")`
- **Delete**: `remove_mcp_server("name")` ( `data/mcp/servers/` ) 
- **LoadAll**: `reload_mcp_servers`