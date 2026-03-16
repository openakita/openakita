# SeeCrab 前端相关 API 接口文档

> **前端源码**: `apps/seecrab/src/`
> **后端路由**: `src/seeagent/api/routes/seecrab.py`
> **路由前缀**: `/api/seecrab`
> **开发代理**: Vite `localhost:5174` → 后端 `127.0.0.1:18900`

SeeCrab 前端是独立的轻量级 Vue 3 聊天界面，通过 `/api/seecrab/*` 前缀与后端通信。
使用 SeeCrabAdapter 将 Agent 内部事件转换为 SeeCrab 专用的 SSE 事件格式。

---

## 目录

- [1. 接口总览](#1-接口总览)
- [2. 聊天 - SSE 流式接口](#2-聊天---sse-流式接口)
- [3. 会话管理接口](#3-会话管理接口)
  - [3.1 列出会话](#31-列出会话)
  - [3.2 创建会话](#32-创建会话)
  - [3.3 获取会话详情](#33-获取会话详情)
  - [3.4 更新会话](#34-更新会话)
  - [3.5 删除会话](#35-删除会话)
- [4. 用户回答接口](#4-用户回答接口)
- [5. SSE 事件类型参考](#5-sse-事件类型参考)
- [6. Pydantic 数据模型](#6-pydantic-数据模型)
- [7. 前端 API Client 参考](#7-前端-api-client-参考)
- [8. 通信架构图](#8-通信架构图)

---

## 1. 接口总览

SeeCrab 前端**仅使用以下 7 个端点**（全部在 `/api/seecrab/` 下）：

| 方法 | 路径 | 说明 | 前端调用方 |
|------|------|------|------------|
| POST | `/api/seecrab/chat` | **SSE 流式** 聊天 | `sse-client.ts` → `ChatInput.vue` |
| GET | `/api/seecrab/sessions` | 列出会话 | `http-client.ts` → `session.ts` → `App.vue` |
| POST | `/api/seecrab/sessions` | 创建新会话 | `http-client.ts` → `session.ts` → `LeftSidebar.vue`, `ChatInput.vue` |
| GET | `/api/seecrab/sessions/{id}` | 获取会话详情(含消息历史) | `http-client.ts` → `chat.ts` → `ChatArea.vue` |
| PATCH | `/api/seecrab/sessions/{id}` | 更新会话元数据(标题) | `http-client.ts` → `chat.ts` (fire-and-forget) |
| DELETE | `/api/seecrab/sessions/{id}` | 删除会话 | `http-client.ts` → `session.ts` → `LeftSidebar.vue` |
| POST | `/api/seecrab/answer` | 回复 ask_user 事件 | `http-client.ts` → `AskUserBlock.vue` |

---

## 2. 聊天 - SSE 流式接口

### POST `/api/seecrab/chat`

核心聊天接口。通过 SeeCrabAdapter 将 Agent 流水线事件转换为 SeeCrab SSE 格式流式返回。

**Request Body:**
```json
{
  "message": "你好，帮我分析一下这段代码",
  "conversation_id": "seecrab_abc123",
  "client_id": "tab_xyz",
  "thinking_mode": "auto",
  "thinking_depth": "medium",
  "plan_mode": false,
  "endpoint": null,
  "agent_profile_id": null,
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
| `conversation_id` | string | 否 | 会话 ID（空则自动生成 `seecrab_xxxx`） |
| `client_id` | string | 否 | 客户端标识，用于 busy-lock |
| `thinking_mode` | string | 否 | `"auto"` / `"on"` / `"off"` |
| `thinking_depth` | string | 否 | `"low"` / `"medium"` / `"high"` |
| `plan_mode` | bool | 否 | 是否强制 Plan 模式 |
| `endpoint` | string | 否 | 指定 LLM 端点名 |
| `agent_profile_id` | string | 否 | Agent 配置 ID |
| `attachments` | array | 否 | 附件列表 |

**Response**: `text/event-stream`

```
data: {"type": "session_title", "session_id": "seecrab_abc123", "title": "你好，帮我分析一下这..."}
data: {"type": "thinking", "content": "让我看看这段代码..."}
data: {"type": "plan_checklist", "steps": [{"title": "分析代码结构", "status": "pending"}]}
data: {"type": "step_card", "stepId": "step_001", "title": "read_file", "status": "running", "sourceType": "tool"}
data: {"type": "step_card", "stepId": "step_001", "title": "read_file", "status": "completed", "output": "..."}
data: {"type": "ai_text", "content": "这段代码的主要问题是..."}
data: {"type": "done"}
```

**Response Headers:**
```
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
Content-Type: text/event-stream
```

**Response 409 (会话忙碌):**
```json
{ "error": "Another request is already processing this conversation" }
```

**Response 503:**
```json
{ "error": "Agent not initialized" }
```

**特殊行为:**
- 首条消息时自动发出 `session_title` 事件（截取前 30 字符）
- 自动持久化会话标题到 session metadata
- 客户端断连时自动触发 `cancel_current_task`
- Busy-lock 机制：同一会话同时只允许一个请求（10 分钟超时自动释放）

**Demo (curl):**
```bash
curl -N -X POST http://localhost:18900/api/seecrab/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好",
    "conversation_id": "test_001",
    "thinking_mode": "auto"
  }'
```

**Demo (JavaScript - 前端实际用法):**
```javascript
import { sseClient } from '@/api/sse-client'
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/session'

const chatStore = useChatStore()
const sessionStore = useSessionStore()

// 确保有活跃会话
if (!sessionStore.activeSessionId) {
  await sessionStore.createSession()
}

// 发送消息并接收 SSE 流
await sseClient.sendMessage(
  '帮我分析一下这段代码的性能问题',
  sessionStore.activeSessionId,
  { thinking_mode: 'auto' }
)

// 取消正在进行的请求
sseClient.abort()
```

**Demo (原生 Fetch SSE):**
```javascript
const resp = await fetch('/api/seecrab/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: '解释一下这段代码',
    conversation_id: 'seecrab_demo_001',
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

      switch (event.type) {
        case 'session_title':
          console.log('会话标题:', event.title)
          break
        case 'thinking':
          console.log('思考中:', event.content)
          break
        case 'ai_text':
          process.stdout.write(event.content)
          break
        case 'step_card':
          console.log(`工具 [${event.title}]: ${event.status}`)
          break
        case 'ask_user':
          console.log('Agent 提问:', event.question)
          break
        case 'error':
          console.error('错误:', event.message)
          break
        case 'done':
          console.log('\n--- 完成 ---')
          break
      }
    }
  }
}
```

---

## 3. 会话管理接口

### 3.1 列出会话

#### GET `/api/seecrab/sessions`

按最近活跃时间倒序返回所有 SeeCrab 会话。

**Response 200:**
```json
{
  "sessions": [
    {
      "id": "seecrab_abc123",
      "title": "代码分析",
      "updated_at": 1710000000000,
      "message_count": 6,
      "last_message": "好的，我来帮你分析这段代码..."
    },
    {
      "id": "seecrab_def456",
      "title": "Python 排序",
      "updated_at": 1709999000000,
      "message_count": 4,
      "last_message": "这是快速排序的实现..."
    }
  ]
}
```

**Demo:**
```bash
curl http://localhost:18900/api/seecrab/sessions
```

---

### 3.2 创建会话

#### POST `/api/seecrab/sessions`

创建一个新的空白会话。

**Request Body:** 无（空 POST）

**Response 200:**
```json
{ "session_id": "seecrab_7a8b9c0d1e2f" }
```

**Demo:**
```bash
curl -X POST http://localhost:18900/api/seecrab/sessions
```

---

### 3.3 获取会话详情

#### GET `/api/seecrab/sessions/{session_id}`

获取会话元数据和完整消息历史，用于切换会话时恢复状态。

**Response 200:**
```json
{
  "session_id": "seecrab_abc123",
  "title": "代码分析",
  "messages": [
    {
      "role": "user",
      "content": "帮我分析一下这段代码",
      "timestamp": 1710000000,
      "metadata": {}
    },
    {
      "role": "assistant",
      "content": "好的，这段代码主要做了以下几件事...",
      "timestamp": 1710000010,
      "metadata": {}
    }
  ]
}
```

**Response 404:**
```json
{ "error": "Session not found" }
```

**Demo:**
```bash
curl http://localhost:18900/api/seecrab/sessions/seecrab_abc123
```

---

### 3.4 更新会话

#### PATCH `/api/seecrab/sessions/{session_id}`

更新会话元数据（目前仅支持标题）。前端在聊天过程中 fire-and-forget 调用。

**Request Body:**
```json
{ "title": "Python 性能优化分析" }
```

**Response 200:**
```json
{ "status": "ok" }
```

**Response 404:**
```json
{ "error": "Session not found" }
```

**Demo:**
```bash
curl -X PATCH http://localhost:18900/api/seecrab/sessions/seecrab_abc123 \
  -H "Content-Type: application/json" \
  -d '{"title": "新的会话标题"}'
```

---

### 3.5 删除会话

#### DELETE `/api/seecrab/sessions/{session_id}`

删除指定会话及其消息历史。

**Response 200:**
```json
{ "status": "ok" }
```

**Response 404:**
```json
{ "error": "Session not found" }
```

**Demo:**
```bash
curl -X DELETE http://localhost:18900/api/seecrab/sessions/seecrab_abc123
```

---

## 4. 用户回答接口

### POST `/api/seecrab/answer`

当 Agent 通过 `ask_user` 事件向用户提问时，通过此接口提交回答。

> **注意**: 当前实现中，此接口仅返回确认信息并提示将回答作为新的 `/api/seecrab/chat` 消息发送。

**Request Body:**
```json
{
  "conversation_id": "seecrab_abc123",
  "answer": "选择方案 A",
  "client_id": "tab_xyz"
}
```

**Response 200:**
```json
{
  "status": "ok",
  "conversation_id": "seecrab_abc123",
  "answer": "选择方案 A",
  "hint": "Please send the answer as a new /api/seecrab/chat message with the same conversation_id"
}
```

**Demo:**
```bash
curl -X POST http://localhost:18900/api/seecrab/answer \
  -H "Content-Type: application/json" \
  -d '{"conversation_id": "seecrab_abc123", "answer": "是的，请继续"}'
```

---

## 5. SSE 事件类型参考

`POST /api/seecrab/chat` 返回的 SSE 事件类型（由 SeeCrabAdapter 转换生成）：

| 事件类型 | 说明 | 关键字段 | 前端处理组件 |
|----------|------|----------|-------------|
| `session_title` | 会话标题（首条消息自动生成） | `session_id`, `title` | `chat.ts` store |
| `thinking` | AI 深度思考内容 | `content` | `BotReply.vue` |
| `plan_checklist` | 执行计划清单 | `steps[]` | `BotReply.vue` |
| `step_card` | 工具/技能执行卡片 | `stepId`, `title`, `status`, `sourceType`, `cardType`, `input`, `output`, `absorbedCalls` | `StepCard.vue` |
| `ai_text` | AI 文本回复增量 | `content` | `BotReply.vue` |
| `ask_user` | Agent 向用户提问 | `ask_id`, `question`, `options[]` | `AskUserBlock.vue` |
| `agent_header` | Agent 身份标识 | - | `BotReply.vue` |
| `artifact` | 生成的产物(文件/图片) | `artifact_type`, `file_url`, `filename`, `mime_type` | `BotReply.vue` |
| `timer_update` | 执行计时更新 | - | `useTimer.ts` |
| `heartbeat` | 心跳保活 | - | (忽略) |
| `done` | 回复完成 | - | `chat.ts` store |
| `error` | 错误 | `message`, `code` | `chat.ts` store |

**step_card 的 status 值:**
- `running` — 正在执行
- `completed` — 执行成功
- `error` — 执行失败

---

## 6. Pydantic 数据模型

> **源码**: `src/seeagent/api/schemas_seecrab.py`

### SeeCrabChatRequest

```python
class SeeCrabChatRequest(BaseModel):
    message: str = ""                          # 用户消息
    conversation_id: str | None = None         # 会话 ID
    agent_profile_id: str | None = None        # Agent 配置 ID
    endpoint: str | None = None                # 指定 LLM 端点
    thinking_mode: str | None = None           # "auto" | "on" | "off"
    thinking_depth: str | None = None          # "low" | "medium" | "high"
    plan_mode: bool = False                    # 是否强制 Plan 模式
    attachments: list[AttachmentInfo] | None   # 附件列表
    client_id: str | None = None               # 客户端标识
```

### SeeCrabSessionUpdateRequest

```python
class SeeCrabSessionUpdateRequest(BaseModel):
    title: str | None = None                   # 新标题
```

### SeeCrabAnswerRequest

```python
class SeeCrabAnswerRequest(BaseModel):
    conversation_id: str                       # 会话 ID（必填）
    answer: str                                # 用户回答（必填）
    client_id: str | None = None               # 客户端标识
```

### AttachmentInfo (共用)

```python
class AttachmentInfo(BaseModel):
    type: str                  # "image" | "file" | "voice"
    name: str                  # 文件名
    url: str | None = None     # URL 或 data URI
    mime_type: str | None = None
```

---

## 7. 前端 API Client 参考

### HTTP Client

> **源码**: `apps/seecrab/src/api/http-client.ts`

```typescript
const BASE = '/api/seecrab'

export const httpClient = {
  // 列出所有会话
  listSessions: () =>
    request<{ sessions: any[] }>('/sessions'),

  // 创建新会话
  createSession: () =>
    request<{ session_id: string }>('/sessions', { method: 'POST' }),

  // 获取会话详情（含消息历史）
  getSession: (id: string) =>
    request<{ session_id: string; title: string; messages: any[] }>(`/sessions/${id}`),

  // 更新会话标题
  updateSession: (id: string, data: { title?: string }) =>
    request<{ status: string }>(`/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  // 删除会话
  deleteSession: (id: string) =>
    request<{ status: string }>(`/sessions/${id}`, { method: 'DELETE' }),

  // 提交 ask_user 回答
  submitAnswer: (conversationId: string, answer: string) =>
    request('/answer', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, answer }),
    }),
}
```

### SSE Client

> **源码**: `apps/seecrab/src/api/sse-client.ts`

```typescript
export class SSEClient {
  private abortController: AbortController | null = null

  // 发送消息并以 SSE 方式接收流式响应
  async sendMessage(
    message: string,
    conversationId?: string,
    options?: { thinking_mode?: string; thinking_depth?: string },
  ): Promise<void> {
    this.abort()
    this.abortController = new AbortController()

    const resp = await fetch('/api/seecrab/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, conversation_id: conversationId, ...options }),
      signal: this.abortController.signal,
    })

    // 读取 SSE 流并分发事件到 chatStore.dispatchEvent()
    // ...
  }

  // 中止当前请求
  abort(): void {
    this.abortController?.abort()
    this.abortController = null
  }
}

export const sseClient = new SSEClient()
```

### Vite 开发代理

> **源码**: `apps/seecrab/vite.config.ts`

```javascript
server: {
  port: 5174,
  proxy: {
    '/api': {
      target: 'http://127.0.0.1:18900',
      changeOrigin: true,
    },
  },
}
```

---

## 8. 通信架构图

```
┌──────────────────────────────────────────────┐
│  SeeCrab 前端 (Vue 3 + Pinia)                │
│  localhost:5174 (开发) / /seecrab/* (生产)     │
│                                              │
│  ┌─────────────┐  ┌──────────────┐           │
│  │ http-client  │  │  sse-client   │           │
│  │  (REST)      │  │  (SSE 流式)   │           │
│  └──────┬───────┘  └──────┬───────┘           │
│         │                  │                   │
│  ┌──────┴──────────────────┴───────┐           │
│  │         stores (Pinia)          │           │
│  │  session.ts  │  chat.ts  │ ui.ts│           │
│  └──────┬──────────────────┬───────┘           │
│         │                  │                   │
│  ┌──────┴──────────────────┴───────┐           │
│  │        Vue 组件层                │           │
│  │  LeftSidebar │ ChatInput │ ...  │           │
│  └──────────────────────────────────┘          │
└──────────────────┬───────────────────────────┘
                   │  /api/seecrab/*
                   ▼
┌──────────────────────────────────────────────┐
│  SeeAgent 后端 (FastAPI)                      │
│  localhost:18900                               │
│                                              │
│  ┌─────────────────────────────────┐          │
│  │  routes/seecrab.py              │          │
│  │  POST /chat (SSE)               │          │
│  │  GET/POST/PATCH/DELETE /sessions │          │
│  │  POST /answer                   │          │
│  └──────────┬──────────────────────┘          │
│             │                                 │
│  ┌──────────▼──────────────────────┐          │
│  │  SeeCrabAdapter                 │          │
│  │  (Agent 事件 → SeeCrab SSE 格式) │          │
│  └──────────┬──────────────────────┘          │
│             │                                 │
│  ┌──────────▼──────────────────────┐          │
│  │  Agent 核心流水线                │          │
│  │  (Brain → ReasoningEngine →     │          │
│  │   ToolExecutor → SkillManager)  │          │
│  └──────────────────────────────────┘          │
└──────────────────────────────────────────────┘
```

---

> **总端点数**: 7
> **SSE 流式端点**: 1 个 (`POST /api/seecrab/chat`)
> **前端技术栈**: Vue 3 + Pinia + Vite + TypeScript
> **HTTP 客户端**: 原生 Fetch API（无第三方 HTTP 库）
> **生成日期**: 2026-03-16
