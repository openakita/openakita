# MCP 服务器

## 什么是 MCP

**MCP（Model Context Protocol）** 是一种标准协议，让大语言模型能够连接外部工具和数据源。

把它想象成 **AI 的 USB 接口**——只要工具提供了 MCP 适配器，Agent 就能即插即用地调用它，不需要为每个工具写专门的集成代码。

```
Agent ←→ MCP 协议 ←→ 数据库
                  ←→ 文件系统
                  ←→ API 服务
                  ←→ 任何 MCP 兼容工具
```

## 为什么需要 MCP

| 传统方式 | MCP 方式 |
|---------|---------|
| 每个工具需要写代码适配 | 标准协议，自动发现可用工具 |
| 工具列表固定，扩展困难 | 启动即加载，随时增减 |
| 参数格式各异 | 统一的调用接口 |

## 配置 MCP 服务器

[打开 MCP 管理](/web/#/mcp)

点击「添加 MCP 服务器」，填写以下信息：

### 基本信息

| 字段 | 说明 |
|------|------|
| **名称** | 为这个 MCP 服务器起个名字（如"本地数据库"） |
| **描述** | 简要说明它提供什么功能 |
| **启动时自动连接** | 开启后 OpenAkita 启动时自动连接此服务器 |

### 传输协议

MCP 支持三种传输方式，根据你的 MCP 服务器类型选择：

#### stdio（本地进程）

最常见的方式，直接启动一个本地进程通信。

| 字段 | 示例 |
|------|------|
| **命令** | `npx` / `python` / `uvx` |
| **参数** | `-m mcp_server_sqlite --db ./data.db` |
| **环境变量** | `DATABASE_URL=sqlite:///data.db` |

```bash
# 示例：连接 SQLite MCP 服务器
命令: npx
参数: -y @modelcontextprotocol/server-sqlite --db-path ./mydata.db
```

#### streamable_http（HTTP 流）

适用于远程 MCP 服务器。

| 字段 | 示例 |
|------|------|
| **URL** | `http://localhost:8080/mcp` |

#### sse（Server-Sent Events）

兼容旧版 MCP 服务器。

| 字段 | 示例 |
|------|------|
| **URL** | `http://localhost:8080/sse` |

::: tip 如何选择
大多数情况下选 **stdio** 即可。如果 MCP 服务器部署在远程或是 HTTP 服务，选 **streamable_http**。**sse** 仅用于兼容旧版。
:::

## 使用示例

### 连接数据库

```
名称: production-db
传输: stdio
命令: uvx
参数: mcp-server-sqlite --db-path /data/analytics.db
```

配置完成后，Agent 在对话中就可以直接查询数据库：

> "帮我查一下上个月的活跃用户数"

Agent 会自动调用 MCP 提供的 SQL 查询工具。

### 连接文件系统

```
名称: project-files
传输: stdio
命令: npx
参数: -y @modelcontextprotocol/server-filesystem /path/to/project
```

### 连接远程 API 服务

```
名称: internal-api
传输: streamable_http
URL: https://api.example.com/mcp
```

## 管理已连接的服务器

在 [MCP 管理页面](/web/#/mcp) 中，你可以：

- 查看每个服务器的**连接状态**（在线 / 离线 / 错误）
- 查看服务器暴露的**可用工具列表**
- **测试连接**确认配置正确
- **编辑 / 删除**已有配置

连接成功后，MCP 提供的工具会自动出现在 Agent 的可用工具列表中，在[工具与技能配置](/web/#/config/tools)页面也能看到。

## 常见问题

**Q: 连接失败怎么办？**
检查：① 命令路径是否正确 ② 依赖是否已安装（如 `npx` 需要 Node.js）③ 环境变量是否完整

**Q: MCP 工具和内置技能有什么区别？**
内置技能是 OpenAkita 原生集成的能力；MCP 工具通过标准协议动态加载，更灵活但需要额外部署 MCP 服务器。两者可以共存互补。

## 相关页面

- [技能管理](/features/skills) — 管理内置技能和自定义技能
- [工具与技能配置](/web/#/config/tools) — 查看所有可用工具（含 MCP）
- [聊天对话](/features/chat) — 在对话中使用 MCP 工具
- [高级设置](/advanced/advanced) — MCP 超时与重试参数调优
