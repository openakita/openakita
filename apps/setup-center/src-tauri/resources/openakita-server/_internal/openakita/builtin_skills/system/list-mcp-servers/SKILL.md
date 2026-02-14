---
name: list-mcp-servers
description: List all configured MCP servers and their connection status. When you need to check available MCP servers or verify server connections.
system: true
handler: mcp
tool-name: list_mcp_servers
category: MCP
---

# List MCP Servers

列出所有配置的 MCP 服务器及其连接状态。

## Parameters

无参数。

## Returns

- 服务器标识符
- 服务器名称
- 连接状态
- 可用工具数量

## Related Skills

- `call-mcp-tool`: 调用 MCP 工具
- `get-mcp-instructions`: 获取使用说明
