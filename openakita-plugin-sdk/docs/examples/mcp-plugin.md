# MCP 插件示例 / MCP Plugin Example

将外部 MCP 服务封装为 OpenAkita 插件，无需编写 Python 代码。

Wrap an external MCP server as an OpenAkita plugin — no Python code required.

**运行时类型 / Runtime type:** `mcp` | **权限级别 / Permission Level:** Basic

---

## 目录结构 / Directory Structure

```
github-mcp/
  plugin.json
  mcp_config.json
  README.md
```

## plugin.json

```json
{
  "id": "github-mcp",
  "name": "GitHub MCP",
  "version": "1.0.0",
  "type": "mcp",
  "entry": "mcp_config.json",
  "description": "GitHub API via MCP protocol",
  "author": "OpenAkita Team",
  "license": "MIT",
  "permissions": [],
  "requires": {
    "npm": ["@modelcontextprotocol/server-github"]
  },
  "category": "tool",
  "tags": ["github", "mcp", "git"]
}
```

> **注意 / Note:** `type: "mcp"` 的插件不需要 `plugin.py`、不需要 `Plugin` 类、不需要 `tools.register` 权限。工具由 MCP 服务自动注册。
>
> `type: "mcp"` plugins don't need `plugin.py`, `Plugin` class, or `tools.register` permission. Tools are auto-registered by the MCP server.

## mcp_config.json

`mcp_config.json` 定义 MCP 服务的启动配置：

```json
{
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": {
    "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
  },
  "transport": "stdio",
  "description": "GitHub API tools via MCP"
}
```

### mcp_config.json 字段参考 / Field Reference

| 字段 / Field | 类型 / Type | 默认值 / Default | 说明 / Description |
|-------------|------------|-----------------|-------------------|
| `command` | `string` | `""` | 启动命令 / Command to run |
| `args` | `string[]` | `[]` | 命令参数 / Command arguments |
| `env` | `object` | `{}` | 环境变量 / Environment variables |
| `transport` | `string` | `"stdio"` | 传输方式: `"stdio"` 或 `"sse"` / Transport: `"stdio"` or `"sse"` |
| `url` | `string` | `""` | SSE 模式的 URL / URL for SSE transport |
| `headers` | `object` | `{}` | SSE 模式的 HTTP 头 / HTTP headers for SSE |
| `cwd` | `string` | 插件目录 / plugin dir | 工作目录 / Working directory |
| `description` | `string` | manifest 描述 / from manifest | 服务描述 / Service description |

### stdio 模式示例 / stdio Example

```json
{
  "command": "python",
  "args": ["-m", "my_mcp_server"],
  "env": { "API_KEY": "${MY_API_KEY}" },
  "transport": "stdio"
}
```

### SSE 模式示例 / SSE Example

```json
{
  "transport": "sse",
  "url": "http://localhost:3001/sse",
  "headers": { "Authorization": "Bearer ${TOKEN}" }
}
```

---

## 工作原理 / How It Works

1. 宿主发现 `type: "mcp"` 的插件
2. 读取 `mcp_config.json`，构建 `MCPServerConfig` 对象
3. 调用 MCP 客户端的 `add_server()` 注册服务
4. MCP 服务提供的工具自动对 AI 可用

> 若宿主未配置 MCP 客户端，插件会静默跳过注册（记录 warning 日志）。
>
> If no MCP client is configured, registration is silently skipped (warning logged).

---

## 相关文档 / Related

- [../plugin-json.md](../plugin-json.md) — 清单文件规范 / Manifest reference
- [../getting-started.md](../getting-started.md) — 不同 type 的要求 / Requirements by type
