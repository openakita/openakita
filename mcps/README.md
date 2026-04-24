# OpenAkita MCP 配置目录

此目录存放 OpenAkita 专用的 MCP (Model Context Protocol) 服务器配置。

## 目录结构

每个 MCP 服务器是一个子目录，包含:

```
mcps/
├── my-server/
│   ├── SERVER_METADATA.json    # 服务器元数据
│   ├── INSTRUCTIONS.md         # 使用说明 (可选)
│   └── tools/
│       ├── tool1.json          # 工具定义
│       └── tool2.json
└── another-server/
    └── ...
```

## SERVER_METADATA.json 示例

```json
{
  "serverIdentifier": "my-server",
  "serverName": "My Custom Server",
  "serverDescription": "描述服务器功能"
}
```

## tools/*.json 示例

```json
{
  "name": "my_tool",
  "description": "工具描述",
  "inputSchema": {
    "type": "object",
    "properties": {
      "param1": {"type": "string", "description": "参数说明"}
    },
    "required": ["param1"]
  }
}
```

## MCP 加载优先级

1. **项目本地** (`mcps/`) - 优先加载
2. **Cursor 项目级** (`~/.cursor/projects/.../mcps/`) - 合并
3. **Cursor 全局** (`~/.cursor/mcps/`) - 合并

相同 ID 的服务器，项目本地优先。

## 相关资源

- [MCP 规范](https://modelcontextprotocol.io/)
- [Anthropic MCP 文档](https://docs.anthropic.com/en/docs/agents-and-tools/mcp)

