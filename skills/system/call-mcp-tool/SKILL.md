---
name: call-mcp-tool
description: Call MCP server tool for extended capabilities. Check 'MCP Servers' section in system prompt for available servers and tools. When you need to use external service or access specialized functionality.
system: true
handler: mcp
tool-name: call_mcp_tool
category: MCP
---

# Call MCP Tool

Call MCP .

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| server | string | Yes | MCP Server identifier |
| tool_name | string | Yes | Tool name |
| arguments | object | No | Tool arguments, Default {} |

## Usage

View 'MCP Servers' Partial and.

## Examples

```json
{
  "server": "my-server",
  "tool_name": "search",
  "arguments": {"query": "example"}
}
```

## Related Skills

- `list-mcp-servers`: list
- `get-mcp-instructions`: getUse
