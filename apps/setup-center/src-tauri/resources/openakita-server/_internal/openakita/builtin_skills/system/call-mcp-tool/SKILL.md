---
name: call-mcp-tool
description: Call MCP server tool for extended capabilities. Check 'MCP Servers' section in system prompt for available servers and tools. When you need to use external service or access specialized functionality.
system: true
handler: mcp
tool-name: call_mcp_tool
category: MCP
---

# Call MCP Tool

调用 MCP 服务器的工具。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| server | string | 是 | MCP 服务器标识符 |
| tool_name | string | 是 | 工具名称 |
| arguments | object | 否 | 工具参数，默认 {} |

## Usage

查看系统提示中的 'MCP Servers' 部分了解可用的服务器和工具。

## Examples

```json
{
  "server": "my-server",
  "tool_name": "search",
  "arguments": {"query": "example"}
}
```

## Related Skills

- `list-mcp-servers`: 列出可用服务器
- `get-mcp-instructions`: 获取使用说明
