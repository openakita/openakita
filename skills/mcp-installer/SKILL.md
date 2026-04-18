---
name: openakita/skills@mcp-installer
description: Install, configure, and add MCP servers to the OpenAkita system. Use when the user needs to install MCP packages (npm/pip/uvx), connect remote HTTP/SSE MCP services, set up custom local MCP servers, or manage MCP server configuration and lifecycle.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# MCP Installer — 安装与配置 MCP 服务器

## 系统 MCP 架构概述

OpenAkita 使用目录结构管理 MCP 服务器。每个 MCP 服务器是一个独立目录，包含配置和工具定义：

```
<server-name>/
├── SERVER_METADATA.json    # 必需：服务器配置
├── INSTRUCTIONS.md         # 可选：使用说明（复杂服务器建议提供）
└── tools/                  # 可选：工具定义（连接后可自动发现）
    ├── tool1.json
    └── tool2.json
```

### Configuration存储位置

| 位置 | 说明 | 可写 |
|------|------|------|
| `mcps/` | 内置 MCP（随项目发行） | 否 |
| `.mcp/` | 兼容目录 | 否 |
| `data/mcp/servers/` | 用户/AI 添加的配置 | **是** |

**所有新添加的 MCP 服务器写入 `data/mcp/servers/`。**

### 传输协议

| 协议 | 场景 | 必需字段 |
|------|------|---------|
| `stdio` | 本地进程（npx/python/node） | `command` + `args` |
| `streamable_http` | 远程 HTTP 服务 | `url` |
| `sse` | 旧版 MCP 服务器（SSE） | `url` |

---

## Installation流程

### 方式一：使用 `add_mcp_server` 工具（推荐）

系统内置了 `add_mcp_server` 工具，可以直接添加 MCP 服务器：

**stdio 模式（npx 包）：**
```
add_mcp_server(
    name="filesystem",
    transport="stdio",
    command="npx",
    args=["-y", "@anthropic/mcp-server-filesystem", "/path/to/dir"],
    description="文件系统访问"
)
```

**stdio 模式（Python 包）：**
```
add_mcp_server(
    name="my-tool",
    transport="stdio",
    command="python",
    args=["-m", "my_mcp_package"],
    description="我的 MCP 工具",
    env={"API_KEY": "xxx"}
)
```

**stdio 模式（uvx 包）：**
```
add_mcp_server(
    name="my-tool",
    transport="stdio",
    command="uvx",
    args=["my-mcp-package"],
    description="我的 MCP 工具"
)
```

**streamable_http 模式（远程服务）：**
```
add_mcp_server(
    name="remote-api",
    transport="streamable_http",
    url="http://localhost:8080/mcp",
    description="远程 API 服务"
)
```

**sse 模式（旧版兼容）：**
```
add_mcp_server(
    name="legacy-api",
    transport="sse",
    url="http://localhost:8080/sse",
    description="旧版 SSE 服务"
)
```

### 方式二：手动创建配置目录

直接在 `data/mcp/servers/` 下创建目录结构。

**第一步：创建目录**
```bash
mkdir -p data/mcp/servers/<server-name>
```

**第二步：写入 SERVER_METADATA.json**

```json
{
  "serverIdentifier": "<server-name>",
  "serverName": "显示名称",
  "serverDescription": "服务器描述",
  "command": "npx",
  "args": ["-y", "package-name"],
  "env": {},
  "transport": "stdio",
  "url": "",
  "autoConnect": false
}
```

**第三步（可选）：创建 INSTRUCTIONS.md**

为复杂的 MCP 服务器编写使用说明，Agent 可在需要时加载。

**第四步（可选）：预定义工具**

在 `tools/` 下创建工具定义 JSON（如果知道工具列表）：

```json
{
  "name": "tool_name",
  "description": "工具描述",
  "inputSchema": {
    "type": "object",
    "properties": {
      "param1": {
        "type": "string",
        "description": "参数描述"
      }
    },
    "required": ["param1"]
  }
}
```

> 工具定义是可选的——连接服务器后系统会自动发现工具。预定义工具的好处是在未连接时 Agent 也能在系统提示中看到工具列表。

**第五步：加载配置**

手动创建后调用 `reload_mcp_servers` 工具让系统扫描并加载新配置。

---

## SERVER_METADATA.json 完整字段说明

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `serverIdentifier` | string | 是 | 唯一标识符，与目录名一致 |
| `serverName` | string | 是 | 显示名称 |
| `serverDescription` | string | 否 | 简短描述 |
| `command` | string | stdio 必需 | 启动命令（python/npx/node/uvx 等） |
| `args` | string[] | 否 | 命令参数 |
| `env` | object | 否 | 环境变量 |
| `transport` | string | 否 | 传输协议：`stdio`（默认）/`streamable_http`/`sse` |
| `url` | string | HTTP/SSE 必需 | 服务 URL |
| `autoConnect` | boolean | 否 | 启动时自动连接（默认 false） |

兼容格式：`"type": "streamableHttp"` 等价于 `"transport": "streamable_http"`。

---

## 常见 MCP 包安装示例

### npm 包（通过 npx）

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
    description="Puppeteer 浏览器自动化"
)
```

```
add_mcp_server(
    name="sqlite",
    command="npx",
    args=["-y", "@anthropic/mcp-server-sqlite", "path/to/db.sqlite"],
    description="SQLite 数据库"
)
```

### Python 包（通过 python -m 或 uvx）

```
add_mcp_server(
    name="arxiv",
    command="uvx",
    args=["mcp-server-arxiv"],
    description="arXiv 论文搜索"
)
```

```
add_mcp_server(
    name="postgres",
    command="python",
    args=["-m", "mcp_server_postgres", "postgresql://user:pass@localhost/db"],
    description="PostgreSQL 数据库"
)
```

### 远程 HTTP 服务

```
add_mcp_server(
    name="composio",
    transport="streamable_http",
    url="https://mcp.composio.dev/partner/mcp_xxxx",
    description="Composio 集成平台"
)
```

### 本地创建的 MCP 服务器

如果使用 `mcp-builder` 技能创建了自定义 MCP 服务器，**必须**在创建后调用 `add_mcp_server` 注册：

**Python 脚本（使用绝对路径）：**
```
add_mcp_server(
    name="my-custom-tool",
    command="python",
    args=["C:/path/to/my_project/server.py"],
    description="自定义 MCP 工具"
)
```

**Python 模块：**
```
add_mcp_server(
    name="my-custom-tool",
    command="python",
    args=["-m", "my_mcp_project.server"],
    description="自定义 MCP 工具"
)
```

**TypeScript（编译后）：**
```
add_mcp_server(
    name="my-custom-tool",
    command="node",
    args=["C:/path/to/my_project/dist/index.js"],
    description="自定义 MCP 工具"
)
```

> **重要**：本地脚本务必使用**绝对路径**，相对路径可能导致工作目录不对而失败。

---

## Installation前检查清单

1. **确认命令可用**：stdio 模式下检查 `command` 是否在 PATH 中（`which npx`、`which python`）
2. **确认依赖已安装**：npm 包需要 Node.js，Python 包需要对应环境
3. **确认端口/URL 可达**：HTTP/SSE 模式下确认目标 URL 可访问
4. **准备环境变量**：许多 MCP 服务器需要 API Key 等凭证，通过 `env` 字段传入
5. **命名规范**：`serverIdentifier` 使用小写字母和连字符（如 `my-tool`），保持简洁

## Installation后验证

添加后系统会自动尝试连接。如果自动连接失败：

1. 使用 `connect_mcp_server("server-name")` 手动连接
2. 连接成功后使用 `list_mcp_servers` 查看状态
3. 使用 `call_mcp_tool("server-name", "tool_name", {...})` 测试调用

## 故障排查

| 问题 | 可能原因 | 解决方法 |
|------|---------|---------|
| 命令未找到 | 未安装或不在 PATH | 安装对应运行时（Node.js/Python） |
| 连接超时 | 服务器启动慢或卡死 | 增大 `MCP_CONNECT_TIMEOUT`（默认 30s） |
| HTTP 连接失败 | URL 错误或服务未启动 | 确认 URL 正确且服务已运行 |
| 工具为空 | 连接未成功 | 先确保 `connect_mcp_server` 成功 |
| 权限错误 | API Key 缺失或无效 | 检查 `env` 中的凭证配置 |

## 管理操作

- **列出服务器**: `list_mcp_servers`
- **连接**: `connect_mcp_server("name")`
- **断开**: `disconnect_mcp_server("name")`
- **删除**: `remove_mcp_server("name")`（仅 `data/mcp/servers/` 中的配置）
- **重新加载全部**: `reload_mcp_servers`
