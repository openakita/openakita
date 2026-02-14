---
name: get-mcp-instructions
description: Get MCP server detailed usage instructions (INSTRUCTIONS.md). When you need to understand server full capabilities or learn server-specific usage patterns.
system: true
handler: mcp
tool-name: get_mcp_instructions
category: MCP
---

# Get MCP Instructions

获取 MCP 服务器的详细使用说明。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| server | string | 是 | 服务器标识符 |

## Returns

- 服务器功能说明
- 工具使用指南
- 示例和最佳实践

## Related Skills

- `list-mcp-servers`: 列出服务器
- `call-mcp-tool`: 调用工具
