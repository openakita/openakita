# SeeAgent Web API 接口文档（非 SeeCrab 部分）

> **服务端框架**: FastAPI + Uvicorn
> **默认端口**: `18900`
> **OpenAPI 文档**: `/docs` (Swagger UI) | `/redoc` (ReDoc)
> **源码位置**: `src/seeagent/api/`

本文档包含 SeeAgent Web API 中 **与 SeeCrab 前端无关** 的所有接口。
SeeCrab 相关接口请参见 [`seecrab-api.md`](./seecrab-api.md)。

---

## 目录

- [1. 认证模块 (Auth)](#1-认证模块-auth)
- [2. Desktop Chat 对话模块](#2-desktop-chat-对话模块)
  - [2.1 Desktop Chat API](#21-desktop-chat-api)
  - [2.2 Desktop Chat SSE 事件类型](#22-desktop-chat-sse-事件类型)
- [3. 会话管理模块 (Sessions)](#3-会话管理模块-sessions)
- [4. Agent 管理模块 (Agents)](#4-agent-管理模块-agents)
  - [4.1 IM Bot 管理](#41-im-bot-管理)
  - [4.2 Agent Profile 管理](#42-agent-profile-管理)
  - [4.3 Agent 分类管理](#43-agent-分类管理)
  - [4.4 Agent 拓扑与协作](#44-agent-拓扑与协作)
  - [4.5 子 Agent 状态](#45-子-agent-状态)
- [5. 系统配置模块 (Config)](#5-系统配置模块-config)
- [6. LLM 模型管理模块 (Models)](#6-llm-模型管理模块-models)
- [7. 技能管理模块 (Skills)](#7-技能管理模块-skills)
- [8. MCP 服务器管理模块](#8-mcp-服务器管理模块)
- [9. 记忆管理模块 (Memory)](#9-记忆管理模块-memory)
- [10. 定时任务模块 (Scheduler)](#10-定时任务模块-scheduler)
- [11. Token 用量统计模块](#11-token-用量统计模块)
- [12. 文件上传模块 (Upload)](#12-文件上传模块-upload)
- [13. IM 通道模块](#13-im-通道模块)
- [14. 日志模块 (Logs)](#14-日志模块-logs)
- [15. 健康检查与诊断模块 (Health)](#15-健康检查与诊断模块-health)
- [16. WebSocket 实时事件](#16-websocket-实时事件)
- [17. Identity 身份管理模块](#17-identity-身份管理模块)
- [18. Hub 市场与包管理模块](#18-hub-市场与包管理模块)
- [19. 组织管理模块 (Orgs)](#19-组织管理模块-orgs)
- [20. 工作区备份模块 (Workspace)](#20-工作区备份模块-workspace)
- [21. 反馈与 Bug 报告模块](#21-反馈与-bug-报告模块)
- [22. 系统路由](#22-系统路由)
- [附录 A: 认证机制详解](#附录-a-认证机制详解)
- [附录 B: Pydantic 数据模型](#附录-b-pydantic-数据模型)

---

## 1. 认证模块 (Auth)

> **路由前缀**: `/api/auth`
> **源码**: `src/seeagent/api/routes/auth.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 密码登录，返回 access_token + 设置 refresh cookie |
| POST | `/api/auth/refresh` | 用 refresh cookie 换取新 access_token |
| POST | `/api/auth/logout` | 清除 refresh cookie |
| GET | `/api/auth/check` | 检查当前认证状态 |
| POST | `/api/auth/change-password` | 修改密码（远程需旧密码验证） |
| GET | `/api/auth/password-hint` | 获取密码提示（仅限 localhost） |

### POST `/api/auth/login`

密码登录，获取 access_token。

**Request Body:**
```json
{
  "password": "your-password"
}
```

**Response 200:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

**Response 401:**
```json
{ "detail": "Invalid password" }
```

**Response 429 (限流):**
```json
{ "detail": "Too many login attempts, please try again later" }
```

**Demo (curl):**
```bash
curl -X POST http://localhost:18900/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"password": "my-secret-password"}'
```

### GET `/api/auth/check`

检查当前请求是否已通过认证。

**Response 200:**
```json
{
  "authenticated": true,
  "method": "local",       // "local" | "token" | "refresh_cookie"
  "password_user_set": true
}
```

**Demo:**
```bash
curl http://localhost:18900/api/auth/check \
  -H "Authorization: Bearer <access_token>"
```

### POST `/api/auth/change-password`

**Request Body:**
```json
{
  "current_password": "old-pass",   // 远程访问时必填
  "new_password": "new-pass"
}
```

**Response 200:**
```json
{
  "status": "ok",
  "message": "Password changed. All remote sessions invalidated.",
  "disconnected": 2
}
```

---

## 2. Desktop Chat 对话模块

### 2.1 Desktop Chat API

> **源码**: `src/seeagent/api/routes/chat.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | **SSE 流式** 主聊天端点 |
| GET | `/api/chat/busy` | 查询当前忙碌的会话 |
| POST | `/api/chat/answer` | 回复 ask_user 事件 |
| POST | `/api/chat/cancel` | 取消当前运行的任务 |
| POST | `/api/chat/skip` | 跳过当前工具/步骤 |
| POST | `/api/chat/insert` | 向运行中的任务注入用户消息 |

#### POST `/api/chat` (SSE Streaming)

主聊天接口，通过完整 Agent 流水线流式返回 AI 响应。

**Request Body:**
```json
{
  "message": "帮我写一个 Python 排序函数",
  "conversation_id": "conv_abc123",
  "client_id": "tab_xyz",
  "plan_mode": false,
  "endpoint": null,
  "thinking_mode": "auto",
  "thinking_depth": "medium",
  "agent_profile_id": "default",
  "attachments": [
    {
      "type": "image",
      "name": "screenshot.png",
      "url": "/api/uploads/1710000000_abc12345.png",
      "mime_type": "image/png"
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 是 | 用户消息文本 |
| `conversation_id` | string | 否 | 会话 ID |
| `client_id` | string | 否 | 客户端标识，用于 busy-lock |
| `plan_mode` | bool | 否 | 是否强制 Plan 模式 |
| `endpoint` | string | 否 | 指定 LLM 端点名 |
| `thinking_mode` | string | 否 | `"auto"` / `"on"` / `"off"` |
| `thinking_depth` | string | 否 | `"low"` / `"medium"` / `"high"` |
| `agent_profile_id` | string | 否 | Agent 配置 ID |
| `attachments` | array | 否 | 附件列表 |

**Response**: `text/event-stream`
```
data: {"type": "thinking_start"}
data: {"type": "thinking_delta", "content": "让我分析一下..."}
data: {"type": "thinking_end"}
data: {"type": "text_delta", "content": "好的，"}
data: {"type": "text_delta", "content": "这是一个排序函数："}
data: {"type": "tool_call_start", "tool": "write_file", "input": {...}}
data: {"type": "tool_call_end", "tool": "write_file", "result": "..."}
data: {"type": "done", "usage": {"input_tokens": 1500, "output_tokens": 800, "total_tokens": 2300}}
```

**Headers:**
```
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

**Response 409 (会话忙碌):**
```json
{
  "error": "conversation_busy",
  "conversation_id": "conv_abc123",
  "busy_client_id": "tab_other",
  "message": "该会话正在其他终端进行中，请新建会话或稍后再试"
}
```

**Demo (curl):**
```bash
curl -N -X POST http://localhost:18900/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好",
    "conversation_id": "test_001",
    "client_id": "demo"
  }'
```

**Demo (JavaScript):**
```javascript
const resp = await fetch('/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: '帮我写一个 hello world',
    conversation_id: 'conv_001',
    client_id: 'browser_tab_1',
  }),
})

const reader = resp.body.getReader()
const decoder = new TextDecoder()
let buffer = ''

while (true) {
  const { done, value } = await reader.read()
  if (done) break
  buffer += decoder.decode(value, { stream: true })
  const parts = buffer.split('\n\n')
  buffer = parts.pop() ?? ''
  for (const part of parts) {
    for (const line of part.split('\n')) {
      if (!line.startsWith('data: ')) continue
      const event = JSON.parse(line.slice(6))
      console.log(event.type, event)
    }
  }
}
```

#### POST `/api/chat/cancel`

取消当前任务。

**Request Body:**
```json
{
  "conversation_id": "conv_abc123",
  "reason": "用户手动取消"
}
```

**Response 200:**
```json
{ "status": "ok", "action": "cancel", "reason": "用户手动取消" }
```

#### POST `/api/chat/insert`

向运行中的任务注入消息。支持智能路由：自动识别停止/跳过指令。

**Request Body:**
```json
{
  "conversation_id": "conv_abc123",
  "message": "停下来"
}
```

**Response 200 (智能路由到 cancel):**
```json
{ "status": "ok", "action": "cancel", "reason": "用户发送停止指令: 停下来" }
```

---

### 2.2 Desktop Chat SSE 事件类型

| 事件类型 | 说明 | 关键字段 |
|----------|------|----------|
| `thinking_start` | 开始深度思考 | - |
| `thinking_delta` | 思考内容增量 | `content` |
| `thinking_end` | 思考结束 | - |
| `text_delta` | AI 回复文本增量 | `content` |
| `tool_call_start` | 工具调用开始 | `tool`, `input` |
| `tool_call_end` | 工具调用结束 | `tool`, `result` |
| `plan_created` | 计划创建 | `plan` |
| `plan_step_updated` | 计划步骤更新 | `step_index`, `status` |
| `ask_user` | 向用户提问 | `question`, `options`, `questions` |
| `agent_switch` | Agent 切换 | `from`, `to` |
| `agent_handoff` | Agent 任务交接 | - |
| `artifact` | 生成产物（文件/图片） | `artifact_type`, `file_url`, `name` |
| `ui_preference` | UI 偏好设置 | 动态键值 |
| `error` | 错误 | `message` |
| `done` | 完成 | `usage` (token 统计) |

---

## 3. 会话管理模块 (Sessions)

> **源码**: `src/seeagent/api/routes/sessions.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sessions` | 列出会话（默认 desktop 通道） |
| GET | `/api/sessions/{id}/history` | 获取消息历史 |
| DELETE | `/api/sessions/{id}` | 删除会话 |
| POST | `/api/sessions/{id}/messages` | 追加消息 |
| POST | `/api/sessions/generate-title` | LLM 生成对话标题 |

#### GET `/api/sessions`

**Query Params:**
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `channel` | string | `"desktop"` | 通道名称 |

**Response 200:**
```json
{
  "sessions": [
    {
      "id": "conv_abc123",
      "title": "Python 排序函数",
      "lastMessage": "好的，这是排序实现...",
      "timestamp": 1710000000000,
      "messageCount": 4,
      "agentProfileId": "default"
    }
  ]
}
```

#### GET `/api/sessions/{id}/history`

**Query Params:**
| 参数 | 类型 | 默认值 |
|------|------|--------|
| `channel` | string | `"desktop"` |
| `user_id` | string | `"desktop_user"` |

**Response 200:**
```json
{
  "messages": [
    {
      "id": "restored-conv_abc-0",
      "role": "user",
      "content": "帮我写排序",
      "timestamp": 1710000000000,
      "chain_summary": null,
      "tool_summary": null,
      "artifacts": [],
      "ask_user": null
    }
  ]
}
```

#### POST `/api/sessions/generate-title`

使用 LLM 生成简洁对话标题。

**Request Body:**
```json
{
  "message": "帮我用 Python 写一个快速排序算法",
  "reply": "好的，这是快速排序的实现..."
}
```

**Response 200:**
```json
{ "title": "Python快速排序" }
```

**Demo:**
```bash
curl -X POST http://localhost:18900/api/sessions/generate-title \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我分析一下这段代码的性能问题"}'
```

---

## 4. Agent 管理模块 (Agents)

> **源码**: `src/seeagent/api/routes/agents.py`

### 4.1 IM Bot 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agents/bots` | 列出所有 Bot |
| POST | `/api/agents/bots` | 创建 Bot |
| PUT | `/api/agents/bots/{bot_id}` | 更新 Bot |
| DELETE | `/api/agents/bots/{bot_id}` | 删除 Bot |
| POST | `/api/agents/bots/{bot_id}/toggle` | 启用/禁用 Bot |
| GET | `/api/agents/env-bots` | 列出 .env 中配置的 Bot |
| POST | `/api/agents/bots/migrate-from-env` | 从 .env 迁移 Bot |

**支持的 Bot 类型:** `feishu`, `telegram`, `dingtalk`, `wework`, `wework_ws`, `onebot`, `onebot_reverse`, `qqbot`

#### POST `/api/agents/bots`

**Request Body:**
```json
{
  "id": "my-telegram-bot",
  "type": "telegram",
  "name": "My Telegram Bot",
  "agent_profile_id": "default",
  "enabled": true,
  "credentials": {
    "bot_token": "123456:ABC-DEF...",
    "webhook_url": "",
    "proxy": ""
  }
}
```

**Response 200:**
```json
{ "status": "ok", "bot": { ... } }
```

### 4.2 Agent Profile 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agents/profiles` | 列出 Agent 配置文件 |
| POST | `/api/agents/profiles` | 创建自定义 Agent |
| PUT | `/api/agents/profiles/{id}` | 更新 Agent |
| DELETE | `/api/agents/profiles/{id}` | 删除自定义 Agent |
| POST | `/api/agents/profiles/{id}/reset` | 重置系统 Agent 为默认值 |
| PATCH | `/api/agents/profiles/{id}/visibility` | 显示/隐藏 Agent |

#### POST `/api/agents/profiles`

**Request Body:**
```json
{
  "id": "code-reviewer",
  "name": "代码审查员",
  "description": "专注代码质量审查",
  "icon": "🔍",
  "color": "#3b82f6",
  "skills": ["code_review", "security_scan"],
  "skills_mode": "inclusive",
  "custom_prompt": "你是一个严格的代码审查员...",
  "category": "development"
}
```

**Response 200:**
```json
{
  "status": "ok",
  "profile": {
    "id": "code-reviewer",
    "name": "代码审查员",
    "type": "custom",
    ...
  }
}
```

### 4.3 Agent 分类管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agents/categories` | 列出所有分类 |
| POST | `/api/agents/categories` | 创建分类 |
| DELETE | `/api/agents/categories/{id}` | 删除分类 |

### 4.4 Agent 拓扑与协作

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agents/topology` | 获取 Agent 网络拓扑（可视化用） |
| GET | `/api/agents/health` | 获取 Orchestrator 健康指标 |
| GET | `/api/agents/collaboration/{session_id}` | 获取会话协作信息 |

#### GET `/api/agents/topology`

返回所有活跃/休眠 Agent 节点及其关系边，供神经网络风格的仪表盘可视化。

**Response 200:**
```json
{
  "nodes": [
    {
      "id": "conv_001",
      "profile_id": "default",
      "name": "Default Agent",
      "icon": "🤖",
      "color": "#6b7280",
      "status": "running",
      "is_sub_agent": false,
      "iteration": 3,
      "tools_executed": ["read_file", "write_file"],
      "tools_total": 5,
      "elapsed_s": 12,
      "conversation_title": "帮我写排序函数"
    }
  ],
  "edges": [
    { "from": "conv_001", "to": "conv_001::researcher", "type": "delegate" }
  ],
  "stats": {
    "total_requests": 42,
    "successful": 40,
    "failed": 2,
    "avg_latency_ms": 1200.5
  }
}
```

### 4.5 子 Agent 状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agents/sub-tasks?conversation_id=xxx` | 实时子 Agent 状态（轮询） |
| GET | `/api/agents/sub-records?conversation_id=xxx` | 持久化子 Agent 工作记录 |

---

## 5. 系统配置模块 (Config)

> **源码**: `src/seeagent/api/routes/config.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config/workspace-info` | 工作区信息 |
| GET | `/api/config/env` | 读取 .env 配置（敏感值已脱敏） |
| POST | `/api/config/env` | 更新 .env 配置 |
| GET | `/api/config/endpoints` | 读取 LLM 端点配置 |
| POST | `/api/config/endpoints` | 写入 LLM 端点配置 |
| POST | `/api/config/reload` | 热重载 LLM 端点 |
| POST | `/api/config/restart` | 触发服务优雅重启 |
| GET | `/api/config/skills` | 读取技能配置 |
| POST | `/api/config/skills` | 写入技能配置 |
| GET | `/api/config/disabled-views` | 读取禁用的 UI 视图 |
| POST | `/api/config/disabled-views` | 设置禁用的 UI 视图 |
| GET | `/api/config/agent-mode` | 获取多 Agent 模式状态 |
| POST | `/api/config/agent-mode` | 切换多 Agent 模式 |
| GET | `/api/config/providers` | 列出 LLM 服务商 |
| POST | `/api/config/list-models` | 拉取端点的模型列表 |

#### POST `/api/config/env`

合并更新 .env 文件，保留注释和排序。

**Request Body:**
```json
{
  "entries": {
    "ANTHROPIC_API_KEY": "sk-ant-xxx",
    "DEFAULT_MODEL": "claude-sonnet-4-20250514",
    "THINKING_MODE": "auto"
  }
}
```

**Response 200:**
```json
{
  "status": "ok",
  "updated_keys": ["ANTHROPIC_API_KEY", "DEFAULT_MODEL", "THINKING_MODE"]
}
```

#### POST `/api/config/reload`

热重载 LLM 端点配置，无需重启服务。

**Response 200:**
```json
{
  "status": "ok",
  "reloaded": true,
  "endpoints": 3,
  "compiler_reloaded": true,
  "stt_reloaded": false
}
```

#### POST `/api/config/agent-mode`

**Request Body:**
```json
{ "enabled": true }
```

**Response 200:**
```json
{ "status": "ok", "multi_agent_enabled": true }
```

#### POST `/api/config/list-models`

**Request Body:**
```json
{
  "api_type": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-xxx",
  "provider_slug": "openai"
}
```

**Response 200:**
```json
{
  "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]
}
```

---

## 6. LLM 模型管理模块 (Models)

> **源码**: `src/seeagent/api/routes/chat_models.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/models` | 列出可用的 LLM 端点/模型及状态 |

**Response 200:**
```json
{
  "models": [
    {
      "name": "claude-sonnet",
      "provider": "anthropic",
      "model": "claude-sonnet-4-20250514",
      "status": "healthy",
      "has_api_key": true
    }
  ]
}
```

---

## 7. 技能管理模块 (Skills)

> **源码**: `src/seeagent/api/routes/skills.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skills` | 列出所有技能（含启用状态） |
| POST | `/api/skills/config` | 更新技能配置 |
| POST | `/api/skills/install` | 安装技能 |
| POST | `/api/skills/uninstall` | 卸载技能 |
| POST | `/api/skills/reload` | 热重载技能 |
| GET | `/api/skills/content/{name}` | 读取 SKILL.md 内容 |
| PUT | `/api/skills/content/{name}` | 编辑 SKILL.md 并热重载 |
| GET | `/api/skills/marketplace` | 搜索技能市场 |

#### GET `/api/skills`

**Response 200:**
```json
{
  "skills": [
    {
      "skill_id": "web_search",
      "name": "web_search",
      "description": "Search the web for information",
      "name_i18n": "网页搜索",
      "description_i18n": "搜索网页获取信息",
      "system": true,
      "enabled": true,
      "category": "information",
      "tool_name": "web_search",
      "config": null,
      "path": "/path/to/SKILL.md",
      "source_url": null
    }
  ]
}
```

#### POST `/api/skills/install`

**Request Body:**
```json
{ "url": "github:user/repo/skill-name" }
```

**Response 200:**
```json
{ "status": "ok", "url": "github:user/repo/skill-name" }
```

**Demo:**
```bash
# 安装技能
curl -X POST http://localhost:18900/api/skills/install \
  -H "Content-Type: application/json" \
  -d '{"url": "github:seeagent/skills/translator"}'

# 卸载技能
curl -X POST http://localhost:18900/api/skills/uninstall \
  -H "Content-Type: application/json" \
  -d '{"skill_id": "translator"}'

# 热重载所有技能
curl -X POST http://localhost:18900/api/skills/reload \
  -H "Content-Type: application/json" \
  -d '{}'
```

---

## 8. MCP 服务器管理模块

> **源码**: `src/seeagent/api/routes/mcp.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/mcp/servers` | 列出 MCP 服务器（含连接状态） |
| POST | `/api/mcp/connect` | 连接到 MCP 服务器 |
| POST | `/api/mcp/disconnect` | 断开 MCP 服务器 |
| GET | `/api/mcp/tools` | 列出 MCP 工具 |
| GET | `/api/mcp/instructions/{name}` | 获取服务器指令文档 |
| POST | `/api/mcp/servers/add` | 添加 MCP 服务器配置 |
| DELETE | `/api/mcp/servers/{name}` | 删除 MCP 服务器配置 |

#### GET `/api/mcp/servers`

**Response 200:**
```json
{
  "mcp_enabled": true,
  "servers": [
    {
      "name": "filesystem",
      "description": "文件系统操作",
      "transport": "stdio",
      "connected": true,
      "tools": [
        { "name": "read_file", "description": "Read file contents" }
      ],
      "tool_count": 5,
      "source": "builtin",
      "removable": false
    }
  ],
  "total": 3,
  "connected": 2,
  "workspace_path": "/path/to/data/mcp/servers"
}
```

#### POST `/api/mcp/servers/add`

**Request Body:**
```json
{
  "name": "my-mcp-server",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@mcp/my-server"],
  "env": { "API_KEY": "xxx" },
  "description": "我的 MCP 服务器",
  "auto_connect": true
}
```

**Response 200:**
```json
{
  "status": "ok",
  "server": "my-mcp-server",
  "path": "/path/to/data/mcp/servers/my-mcp-server",
  "connect_result": { "connected": true, "tool_count": 3 }
}
```

---

## 9. 记忆管理模块 (Memory)

> **路由前缀**: `/api/memories`
> **源码**: `src/seeagent/api/routes/memory.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memories` | 列出记忆（支持搜索/筛选） |
| GET | `/api/memories/stats` | 记忆统计 |
| POST | `/api/memories` | 创建记忆 |
| GET | `/api/memories/{id}` | 获取记忆详情 |
| PUT | `/api/memories/{id}` | 更新记忆 |
| DELETE | `/api/memories/{id}` | 删除记忆 |
| POST | `/api/memories/batch-delete` | 批量删除 |
| POST | `/api/memories/review` | LLM 审查记忆 |
| POST | `/api/memories/refresh-md` | 重新生成 MEMORY.md |

#### GET `/api/memories`

**Query Params:**
| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | 按类型筛选 (fact/preference/...) |
| `search` | string | 语义搜索 |
| `min_score` | float | 最低重要性分数 |
| `limit` | int | 返回数量限制 (默认 200) |

**Response 200:**
```json
{
  "memories": [
    {
      "id": "mem_abc123",
      "type": "fact",
      "priority": "high",
      "content": "用户偏好使用 Python",
      "subject": "user",
      "predicate": "prefers",
      "tags": ["preference", "language"],
      "importance_score": 0.9,
      "confidence": 0.85,
      "access_count": 5,
      "created_at": "2025-03-15T10:00:00",
      "updated_at": "2025-03-16T08:00:00"
    }
  ],
  "total": 1
}
```

#### POST `/api/memories`

**Request Body:**
```json
{
  "type": "fact",
  "content": "用户是后端开发工程师",
  "subject": "user",
  "predicate": "is",
  "importance_score": 0.8,
  "tags": ["user_info"]
}
```

---

## 10. 定时任务模块 (Scheduler)

> **源码**: `src/seeagent/api/routes/scheduler.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/scheduler/tasks` | 列出所有定时任务 |
| GET | `/api/scheduler/tasks/{id}` | 获取任务详情 |
| POST | `/api/scheduler/tasks` | 创建定时任务 |
| PUT | `/api/scheduler/tasks/{id}` | 更新任务 |
| DELETE | `/api/scheduler/tasks/{id}` | 删除任务 |
| POST | `/api/scheduler/tasks/{id}/toggle` | 启用/禁用任务 |
| POST | `/api/scheduler/tasks/{id}/trigger` | 立即触发任务 |
| GET | `/api/scheduler/channels` | 列出可用 IM 通道 |
| GET | `/api/scheduler/stats` | 调度器统计 |

#### POST `/api/scheduler/tasks`

**Request Body:**
```json
{
  "name": "每日总结",
  "task_type": "task",
  "trigger_type": "cron",
  "trigger_config": {
    "cron": "0 18 * * 1-5"
  },
  "prompt": "总结今天完成的工作",
  "channel_id": "telegram:my-bot",
  "chat_id": "12345678",
  "enabled": true
}
```

**task_type**: `reminder` | `task`
**trigger_type**: `once` | `interval` | `cron`

**Response 200:**
```json
{
  "status": "ok",
  "task_id": "task_abc123",
  "task": { ... }
}
```

---

## 11. Token 用量统计模块

> **路由前缀**: `/api/stats/tokens`
> **源码**: `src/seeagent/api/routes/token_stats.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/stats/tokens/summary` | 聚合统计（按维度分组） |
| GET | `/api/stats/tokens/timeline` | 时间序列（图表用） |
| GET | `/api/stats/tokens/sessions` | 按会话分组 |
| GET | `/api/stats/tokens/total` | 总计 |
| GET | `/api/stats/tokens/by-agent` | 按 Agent 分组 |
| GET | `/api/stats/tokens/context` | 当前上下文大小 + 限制 |

**通用 Query Params:**
| 参数 | 类型 | 说明 |
|------|------|------|
| `period` | string | 时间范围: `1d`, `3d`, `1w`, `1m`, `6m`, `1y` |
| `start` | string | 起始时间 (ISO 8601) |
| `end` | string | 结束时间 (ISO 8601) |
| `endpoint_name` | string | 按端点名筛选 |

#### GET `/api/stats/tokens/summary`

**额外参数:** `group_by` (默认 `endpoint_name`), `operation_type`

**Response 200:**
```json
{
  "start": "2025-03-15 00:00:00",
  "end": "2025-03-16 00:00:00",
  "group_by": "endpoint_name",
  "data": [
    {
      "group": "claude-sonnet",
      "input_tokens": 50000,
      "output_tokens": 20000,
      "total_tokens": 70000,
      "request_count": 15
    }
  ]
}
```

#### GET `/api/stats/tokens/context`

**Response 200:**
```json
{
  "context_tokens": 45000,
  "context_limit": 200000,
  "percent": 22.5
}
```

**Demo:**
```bash
# 最近 7 天按端点分组
curl "http://localhost:18900/api/stats/tokens/summary?period=1w&group_by=endpoint_name"

# 按小时时间线
curl "http://localhost:18900/api/stats/tokens/timeline?period=1d&interval=hour"
```

---

## 12. 文件上传模块 (Upload)

> **源码**: `src/seeagent/api/routes/upload.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload` | 上传文件（最大 50MB） |
| GET | `/api/uploads/{filename}` | 下载/访问已上传文件 |

**限制:**
- 最大文件大小: 50 MB
- 禁止的扩展名: `.exe`, `.bat`, `.cmd`, `.com`, `.scr`, `.pif`, `.msi`, `.sh`, `.ps1`

#### POST `/api/upload`

**Request:** `multipart/form-data`
```bash
curl -X POST http://localhost:18900/api/upload \
  -F "file=@screenshot.png"
```

**Response 200:**
```json
{
  "status": "ok",
  "filename": "1710000000_abc12345.png",
  "original_name": "screenshot.png",
  "size": 204800,
  "content_type": "image/png",
  "url": "/api/uploads/1710000000_abc12345.png"
}
```

---

## 13. IM 通道模块

> **源码**: `src/seeagent/api/routes/im.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/im/channels` | 列出所有 IM 通道及在线状态 |
| GET | `/api/im/sessions` | 列出指定通道的会话 |
| GET | `/api/im/sessions/{id}/messages` | 获取会话消息 |

#### GET `/api/im/channels`

**Response 200:**
```json
{
  "channels": [
    {
      "channel": "telegram:my-bot",
      "name": "Telegram Bot",
      "status": "online",
      "sessionCount": 5,
      "lastActive": "2025-03-16T10:00:00"
    }
  ]
}
```

#### GET `/api/im/sessions`

**Query Params:** `channel` (通道名)

#### GET `/api/im/sessions/{id}/messages`

**Query Params:** `limit` (默认 50, 最大 200), `offset`

---

## 14. 日志模块 (Logs)

> **源码**: `src/seeagent/api/routes/logs.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/logs/service` | 后端服务日志尾部 |
| POST | `/api/logs/frontend` | 上报前端日志 |
| GET | `/api/logs/frontend` | 前端日志尾部 |
| GET | `/api/logs/combined` | 合并前后端日志 |

**通用参数:** `tail_bytes` (默认 60000, 最大 400000)

#### POST `/api/logs/frontend`

**Request Body:**
```json
{
  "lines": [
    "[2025-03-16T10:00:00] [INFO] App mounted",
    "[2025-03-16T10:00:01] [ERROR] WebSocket disconnect"
  ]
}
```

**Response 200:**
```json
{ "ok": true, "written": 2 }
```

---

## 15. 健康检查与诊断模块 (Health)

> **源码**: `src/seeagent/api/routes/health.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 基础健康检查 |
| POST | `/api/health/check` | LLM 端点健康检查（dry_run） |
| GET | `/api/health/loop` | 事件循环健康 + 并发统计 |
| GET | `/api/debug/pool-stats` | Agent 实例池统计 |
| GET | `/api/debug/orchestrator-state` | Orchestrator 内部状态 |
| GET | `/api/diagnostics` | 系统自诊断 |

#### GET `/api/health`

**Response 200:**
```json
{
  "status": "ok",
  "service": "seeagent",
  "version": "0.8.0",
  "git_hash": "07cc532",
  "version_full": "seeagent v0.8.0 (07cc532)",
  "pid": 12345,
  "timestamp": "2025-03-16T10:00:00",
  "agent_initialized": true,
  "local_ip": "192.168.1.100"
}
```

#### POST `/api/health/check`

对 LLM 端点发起只读健康检查。

**Request Body:**
```json
{ "endpoint_name": "claude-sonnet" }
```

**Response 200:**
```json
{
  "results": [
    {
      "name": "claude-sonnet",
      "status": "healthy",
      "latency_ms": 320,
      "error": null,
      "consecutive_failures": 0,
      "cooldown_remaining": 0,
      "last_checked_at": "2025-03-16T10:00:00"
    }
  ]
}
```

#### GET `/api/diagnostics`

**Response 200:**
```json
{
  "summary": "healthy",
  "checks": [
    {
      "id": "C1_BUNDLED_RUNTIME",
      "title": "内置运行时",
      "status": "pass",
      "code": "RUNTIME_OK",
      "evidence": ["Python 3.11.9, venv"]
    },
    {
      "id": "C3_CORE",
      "title": "核心引擎",
      "status": "pass",
      "code": "CORE_OK",
      "evidence": ["seeagent 0.8.0"]
    }
  ],
  "environment": {
    "platform": "darwin-arm64",
    "pythonVersion": "3.11.9",
    "runtimeType": "venv",
    "seeagentVersion": "0.8.0",
    "pid": 12345
  }
}
```

---

## 16. WebSocket 实时事件

> **源码**: `src/seeagent/api/routes/websocket.py`

| 端点 | 说明 |
|------|------|
| `ws://host:18900/ws/events?token=<access_token>` | 实时事件推送流 |

**认证:** query param `token=<access_token>` 或 localhost 自动放行

**事件格式:**
```json
{
  "event": "chat:busy",
  "data": { "conversation_id": "conv_001", "client_id": "tab_1" },
  "ts": 1710000000.123
}
```

**事件类型:**

| 事件 | 说明 |
|------|------|
| `connected` | 连接成功 |
| `ping` / `pong` | 心跳保活 (30s 超时) |
| `chat:busy` | 会话开始处理 |
| `chat:idle` | 会话处理完成 |
| `chat:message_update` | 最后消息更新 |
| `skills:changed` | 技能状态变化 |
| `scheduler:task_update` | 定时任务变化 |
| `session_invalidated` | 会话被强制关闭（密码变更后） |

**Demo (JavaScript):**
```javascript
const ws = new WebSocket(`ws://localhost:18900/ws/events?token=${accessToken}`)

ws.onmessage = (event) => {
  const { event: eventType, data, ts } = JSON.parse(event.data)
  console.log(`[${eventType}]`, data)
}

// 客户端心跳
setInterval(() => ws.send('ping'), 25000)

ws.onclose = (event) => {
  if (event.code === 4001) {
    console.log('Session invalidated, need re-login')
  }
}
```

---

## 17. Identity 身份管理模块

> **源码**: `src/seeagent/api/routes/identity.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/identity/files` | 列出身份文件 |
| GET | `/api/identity/files/{name}` | 读取身份文件内容 |
| PUT | `/api/identity/files/{name}` | 写入身份文件 |
| GET | `/api/identity/validate` | 验证身份文件 |
| POST | `/api/identity/compile` | 编译身份文件 |
| POST | `/api/identity/reload` | 重新加载身份 |

身份文件包括: `AGENT.md`, `SOUL.md`, `USER.md`, `MEMORY.md` 等。

---

## 18. Hub 市场与包管理模块

> **源码**: `src/seeagent/api/routes/hub.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/agents/package/export` | 导出 Agent 为 .akita-agent |
| POST | `/api/agents/package/batch-export` | 批量导出为 .zip |
| POST | `/api/agents/package/import` | 导入 Agent 包 |
| POST | `/api/agents/package/inspect` | 预览包内容 |
| GET | `/api/agents/package/exportable` | 列出可导出的 Agent |
| GET | `/api/hub/agents` | 搜索 Agent 商店 |
| GET | `/api/hub/agents/{id}` | 获取 Agent 详情 |
| POST | `/api/hub/agents/{id}/install` | 从 Hub 安装 Agent |
| GET | `/api/hub/skills` | 搜索 Skill 商店 |
| GET | `/api/hub/skills/{id}` | 获取 Skill 详情 |
| POST | `/api/hub/skills/{id}/install` | 从 Hub 安装 Skill |

---

## 19. 组织管理模块 (Orgs)

> **源码**: `src/seeagent/api/routes/orgs.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/orgs` | 列出组织 |
| POST | `/api/orgs` | 创建组织 |
| GET | `/api/orgs/{id}` | 获取组织详情 |
| PUT | `/api/orgs/{id}` | 更新组织 |
| DELETE | `/api/orgs/{id}` | 归档组织 |
| GET | `/api/orgs/templates` | 列出组织模板 |
| GET | `/api/orgs/templates/{id}` | 获取模板详情 |
| POST | `/api/orgs/from-template` | 从模板创建组织 |
| POST | `/api/orgs/import` | 导入组织 |
| POST | `/api/orgs/avatars/upload` | 上传头像 |
| GET | `/api/orgs/avatar-presets` | 获取预设头像列表 |
| POST | `/api/orgs/{id}/nodes` | 添加组织节点 |
| GET | `/api/orgs/{id}/nodes` | 列出组织节点 |
| PUT | `/api/orgs/{id}/nodes/{nid}` | 更新节点 |
| DELETE | `/api/orgs/{id}/nodes/{nid}` | 删除节点 |
| POST | `/api/orgs/commands` | 执行异步命令 |
| GET | `/api/orgs/commands/{id}` | 获取命令状态 |
| POST | `/api/orgs/{id}/inbox` | 发送到组织收件箱 |
| GET | `/api/orgs/{id}/inbox` | 列出收件箱消息 |
| GET | `/api/orgs/{id}/subscribe` | **SSE** 订阅组织事件 |

---

## 20. 工作区备份模块 (Workspace)

> **源码**: `src/seeagent/api/routes/workspace_io.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/workspace/backup-settings` | 读取备份设置 |
| POST | `/api/workspace/backup-settings` | 保存备份设置 |
| POST | `/api/workspace/export` | 创建工作区备份 zip |
| POST | `/api/workspace/import` | 从备份恢复工作区 |
| GET | `/api/workspace/backups` | 列出可用备份 |

---

## 21. 反馈与 Bug 报告模块

> **源码**: `src/seeagent/api/routes/bug_report.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/system-info` | 获取系统环境信息 |
| GET | `/api/feedback-config` | 获取反馈配置 (CAPTCHA 等) |
| POST | `/api/bug-report` | 提交 Bug 报告 |
| POST | `/api/feature-request` | 提交功能建议 |
| GET | `/api/feedback-download/{id}` | 下载反馈 zip 包 |

---

## 22. 系统路由

> **源码**: `src/seeagent/api/server.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 根路由 — 重定向到 `/web/` 或返回 API 信息 |
| POST | `/api/shutdown` | 优雅关闭服务（仅限 localhost） |

**静态文件挂载:**

| 挂载点 | 说明 |
|--------|------|
| `/web/*` | Setup Center 前端 |
| `/seecrab/*` | SeeCrab 前端 |
| `/api/avatars/*` | 头像图片 |

---

## 附录 A: 认证机制详解

### 认证方式

1. **Bearer Token**: `Authorization: Bearer <access_token>`
2. **Refresh Cookie**: `seeagent_refresh` (httpOnly, secure, sameSite=strict)
3. **Query Param**: `?token=<access_token>` (WebSocket 用)
4. **X-API-Key**: `X-API-Key: <access_token>`
5. **本地免认证**: `127.0.0.1` / `::1` 直连自动放行

### Token 生命周期

| 类型 | 有效期 | 存储方式 |
|------|--------|----------|
| Access Token | 24 小时 | 前端 localStorage |
| Refresh Token | 90 天 | httpOnly Cookie |

### 登录限流

- 5 次/60 秒/IP

### 免认证路径

```
/                   # 根路由
/api/health         # 健康检查
/api/auth/*         # 认证路由自身
/api/logs/frontend  # 前端日志上报
/web/*              # 静态资源
/ws/*               # WebSocket (有独立认证)
/docs, /redoc       # API 文档
/openapi.json       # OpenAPI 规范
```

---

## 附录 B: Pydantic 数据模型

> **源码**: `src/seeagent/api/schemas.py`

### ChatRequest

```python
class ChatRequest(BaseModel):
    message: str = ""                        # 用户消息
    conversation_id: str | None = None       # 会话 ID
    plan_mode: bool = False                  # 强制 Plan 模式
    endpoint: str | None = None              # 指定 LLM 端点
    attachments: list[AttachmentInfo] | None  # 附件
    thinking_mode: str | None = None         # "auto" | "on" | "off"
    thinking_depth: str | None = None        # "low" | "medium" | "high"
    agent_profile_id: str | None = None      # Agent 配置 ID
    client_id: str | None = None             # 客户端唯一标识
```

### AttachmentInfo

```python
class AttachmentInfo(BaseModel):
    type: str       # "image" | "file" | "voice"
    name: str       # 文件名
    url: str | None  # URL 或 data URI
    mime_type: str | None
```

### ChatControlRequest

```python
class ChatControlRequest(BaseModel):
    conversation_id: str | None = None
    reason: str = ""       # 取消/跳过原因
    message: str = ""      # 消息（仅 insert 使用）
```

### HealthResult

```python
class HealthResult(BaseModel):
    name: str
    status: str               # "healthy" | "degraded" | "unhealthy"
    latency_ms: float | None
    error: str | None
    consecutive_failures: int = 0
    cooldown_remaining: float = 0
    is_extended_cooldown: bool = False
    last_checked_at: str | None
```

---

> **本文档端点数**: 113+（不含 SeeCrab 的 7 个端点）
> **SSE 流式端点**: 2 个 (`/api/chat`, `/api/orgs/{id}/subscribe`)
> **WebSocket 端点**: 1 个 (`/ws/events`)
> **路由模块文件**: 23 个 Python 模块（不含 `seecrab.py`）
> **SeeCrab 接口文档**: [`seecrab-api.md`](./seecrab-api.md)
> **生成日期**: 2026-03-16
