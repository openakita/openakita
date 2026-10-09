# Parallel Search MCP Server

通过 Parallel Search MCP 搜索网页并提取指定页面的正文，无需 Parallel API Key。
使用 OpenAkita 已有的 Streamable HTTP 连接；默认不会自动连接。

## 安装要求

安装 OpenAkita 后，确保使用 MCP 1.x SDK（当前 OpenAkita 客户端使用该版本的传输 API，
仓库锁定版本为 1.27.2）：

```bash
pip install 'mcp>=1.27.2,<2'
```

## 使用

1. 确保 MCP 已启用（`MCP_ENABLED=true`）。
2. 使用 `connect_mcp_server("parallel-search")` 连接并发现最新工具及参数。
3. 使用 `call_mcp_tool(server, tool_name, arguments)` 调用工具。

搜索示例：

```json
{
  "server": "parallel-search",
  "tool_name": "web_search",
  "arguments": {
    "objective": "Find the official OpenAkita documentation and GitHub repository",
    "search_queries": ["OpenAkita official documentation GitHub"]
  }
}
```

提取页面示例：

```json
{
  "server": "parallel-search",
  "tool_name": "web_fetch",
  "arguments": {
    "urls": ["https://github.com/openakita/openakita"]
  }
}
```

工具及参数以连接后发现的 schema 为准。可使用 `get_mcp_instructions("parallel-search")`
查看本说明，使用 `disconnect_mcp_server("parallel-search")` 断开连接。

## 注意事项

- 匿名端点为 `https://search.parallel.ai/mcp`，无需配置认证头或保存凭据。
- 免费匿名访问有较低的速率限制，适合探索和轻量使用；匿名搜索默认使用 fast 模式。
- 如果传入 `session_id`，为每个对话生成一个 UUID，并在该对话的搜索与提取调用中复用。
- 本配置只提供可选 MCP 工具，不改变现有网页搜索工具的提供商或用户已保存的配置。

更多参数与限制：[Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp)。
