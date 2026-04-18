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

OpenAkita Use目录结构Manage MCP 服务器。每个 MCP 服务器Yes一个独立目录，Includes配置和工具定义：

```
<server-name>/
├── SERVER_METADATA.json    # 必需：服务器配置
├── INSTRUCTIONS.md         # 可选：Use说明（复杂服务器建议Provides）
└── tools/                  # 可选：工具定义（连接后可Automatic发现）
    ├── tool1.json
    └── tool2.json
```

### Configuration存储位置

| 位置 | Description | 可写 |
|------|------|------|
| `mcps/` | 内置 MCP（随项目发行） | No |
| `.mcp/` | 兼容目录 | No |
| `data/mcp/servers/` | 用户/AI 添加的配置 | **Yes** |

**所有新添加的 MCP 服务器Write `data/mcp/servers/`。**

### 传输协议

| 协议 | 场景 | 必需字段 |
|------|------|---------|
| `stdio` | 本地进程（npx/python/node） | `command` + `args` |
| `streamable_http` | 远程 HTTP 服务 | `url` |
| `sse` | 旧版 MCP 服务器（SSE） | `url` |

---

## Installation流程

### 方式一：Use `add_mcp_server` 工具（Recommendations）

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

### 方式二：ManualCreate配置目录

直接在 `data/mcp/servers/` 下Create目录结构。

**第一步：Create目录**
```bash
mkdir -p data/mcp/servers/<server-name>
```

**第二步：Write SERVER_METADATA.json**

```json
{
  "serverIdentifier": "<server-name>",
  "serverName": "Display名称",
  "serverDescription": "服务器描述",
  "command": "npx",
  "args": ["-y", "package-name"],
  "env": {},
  "transport": "stdio",
  "url": "",
  "autoConnect": false
}
```

**第三步（可选）：Create INSTRUCTIONS.md**

为复杂的 MCP 服务器编写Use说明，Agent 可在需要时Load。

**第四步（可选）：预定义工具**

在 `tools/` 下Create工具定义 JSON（如果知道工具列表）：

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

> 工具定义Yes可选的——连接服务器后系统会Automatic发现工具。预定义工具的好处Yes在未连接时 Agent 也能在系统提示中看到工具列表。

**第五步：Load配置**

ManualCreate后Call `reload_mcp_servers` 工具让系统扫描并Load新配置。

---

## SERVER_METADATA.json Full字段说明

| 字段 | Type | 必需 | Description |
|------|------|------|------|
| `serverIdentifier` | string | Yes | 唯一标识符，与目录名一致 |
| `serverName` | string | Yes | Display名称 |
| `serverDescription` | string | No | 简短描述 |
| `command` | string | stdio 必需 | Launch命令（python/npx/node/uvx 等） |
| `args` | string[] | No | 命令参数 |
| `env` | object | No | 环境变量 |
| `transport` | string | No | 传输协议：`stdio`（Default）/`streamable_http`/`sse` |
| `url` | string | HTTP/SSE 必需 | 服务 URL |
| `autoConnect` | boolean | No | Launch时Automatic连接（Default false） |

兼容格式：`"type": "streamableHttp"` 等价于 `"transport": "streamable_http"`。

---

## 常见 MCP 包安装示例

### npm 包（Via npx）

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
    description="Puppeteer 浏览器Automatic化"
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

### Python 包（Via python -m 或 uvx）

```
add_mcp_server(
    name="arxiv",
    command="uvx",
    args=["mcp-server-arxiv"],
    description="arXiv 论文Search"
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

### 本地Create的 MCP 服务器

如果Use `mcp-builder` 技能Create了自定义 MCP 服务器，**必须**在Create后Call `add_mcp_server` 注册：

**Python 脚本（Use绝对路径）：**
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

> **重要**：本地脚本务必Use**绝对路径**，相对路径可能导致Working directory不对而失败。

---

## Installation前检查清单

1. **确认命令可用**：stdio 模式下检查 `command` YesNo在 PATH 中（`which npx`、`which python`）
2. **确认依赖已安装**：npm 包需要 Node.js，Python 包需要对应环境
3. **确认端口/URL 可达**：HTTP/SSE 模式下确认目标 URL 可访问
4. **准备环境变量**：许多 MCP 服务器需要 API Key 等凭证，Via `env` 字段传入
5. **命名规范**：`serverIdentifier` Use小写字母和连字符（如 `my-tool`），保持简洁

## Installation后验证

添加后系统会Automatic尝试连接。如果Automatic连接失败：

1. Use `connect_mcp_server("server-name")` Manual连接
2. 连接成功后Use `list_mcp_servers` View状态
3. Use `call_mcp_tool("server-name", "tool_name", {...})` 测试Call

## 故障排查

| 问题 | 可能原因 | 解决方法 |
|------|---------|---------|
| 命令未找到 | 未安装或不在 PATH | 安装对应Run时（Node.js/Python） |
| 连接超时 | 服务器Launch慢或卡死 | 增大 `MCP_CONNECT_TIMEOUT`（Default 30s） |
| HTTP 连接失败 | URL 错误或服务未Launch | 确认 URL 正确且服务已Run |
| 工具为空 | 连接未成功 | 先确保 `connect_mcp_server` 成功 |
| 权限错误 | API Key 缺失或无效 | 检查 `env` 中的凭证配置 |

## Manage操作

- **List服务器**: `list_mcp_servers`
- **连接**: `connect_mcp_server("name")`
- **断开**: `disconnect_mcp_server("name")`
- **Delete**: `remove_mcp_server("name")`（仅 `data/mcp/servers/` 中的配置）
- **重新LoadAll**: `reload_mcp_servers`
