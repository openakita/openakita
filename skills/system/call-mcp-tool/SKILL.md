---
name: call-mcp-tool
description: Call MCP server tool for extended capabilities. Check 'MCP Servers' section in system prompt for available servers and tools. When you need to use external service or access specialized functionality.
system: true
handler: mcp
tool-name: call_mcp_tool
category: MCP
---

# Call MCP Tool

Call MCP 服务器的工具。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| server | string | Yes | MCP Server identifier |
| tool_name | string | Yes | Tool name |
| arguments | object | No | Tool arguments，Default {} |

## Usage

View系统提示中的 'MCP Servers' Partial了解可用的服务器和工具。

## Examples

```json
{
  "server": "my-server",
  "tool_name": "search",
  "arguments": {"query": "example"}
}
```

## Related Skills

- `list-mcp-servers`: list可用服务器
- `get-mcp-instructions`: getUse说明
