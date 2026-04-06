# POST /api/chat — 流式对话接口

基于 Server-Sent Events (SSE) 的流式 AI 对话接口，与 IM/CLI 通道共享完整的 Agent 流水线。

## 概览

```
POST /api/chat
Content-Type: application/json
Accept: text/event-stream

→ 返回 text/event-stream，逐事件推送 AI 响应
```

## 请求体 (ChatRequest)

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `message` | string | 否 | `""` | 用户消息文本 |
| `conversation_id` | string | 否 | `null` | 会话 ID，用于维持上下文。不传则自动生成 |
| `mode` | string | 否 | `"agent"` | 交互模式：`ask`(只读)、`plan`(先规划再执行)、`agent`(完整执行) |
| `plan_mode` | boolean | 否 | `false` | **已弃用**，等价于 `mode="plan"`，保留用于向后兼容 |
| `endpoint` | string | 否 | `null` | 指定 LLM 端点名称，`null` 为自动选择 |
| `attachments` | array | 否 | `null` | 附件列表，见下方 [AttachmentInfo](#attachmentinfo) |
| `thinking_mode` | string | 否 | `null` | 思考模式覆盖：`auto`(系统决定)、`on`(强制开启)、`off`(强制关闭) |
| `thinking_depth` | string | 否 | `null` | 思考深度：`low`、`medium`、`high`，仅在思考开启时生效 |
| `agent_profile_id` | string | 否 | `null` | Agent 配置 ID，仅在 `multi_agent_enabled=true` 时有效 |
| `client_id` | string | 否 | `null` | 客户端/标签页唯一标识，用于多端忙锁协调 |

### AttachmentInfo

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 附件类型：`image`、`file`、`voice` |
| `name` | string | 是 | 文件名 |
| `url` | string | 否 | URL 或 data URI |
| `mime_type` | string | 否 | MIME 类型 |

### 请求示例

```json
{
  "message": "帮我写一个 Python 快排",
  "conversation_id": "conv_abc123",
  "mode": "agent",
  "thinking_mode": "auto",
  "thinking_depth": "medium"
}
```

## 响应

返回 `text/event-stream`，格式为标准 SSE：

```
data: {"type": "<event_type>", ...payload}

```

每条事件以 `data:` 开头，JSON 格式，以两个换行符结尾。

## SSE 事件类型

### thinking_start

思考过程开始。

```json
{"type": "thinking_start"}
```

### thinking_delta

思考内容增量。

```json
{"type": "thinking_delta", "content": "让我分析一下需求..."}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | string | 本次思考增量文本 |

### thinking_end

思考过程结束。

```json
{"type": "thinking_end"}
```

### text_delta

回复文本增量（核心事件）。

```json
{"type": "text_delta", "content": "以下是快速排序的实现：\n"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | string | 本次文本增量 |

客户端需自行拼接所有 `text_delta` 的 `content` 得到完整回复。

### tool_call_start

工具调用开始。

```json
{"type": "tool_call_start", "tool": "run_shell", "call_id": "call_001", "args_preview": "python -c 'print(1)'"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `tool` | string | 工具名称 |
| `call_id` | string | 调用 ID |
| `args_preview` | string | 参数预览（截断） |

### tool_call_end

工具调用结束。

```json
{"type": "tool_call_end", "tool": "run_shell", "call_id": "call_001", "result": "1\n", "status": "success"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `tool` | string | 工具名称 |
| `call_id` | string | 调用 ID |
| `result` | string | 工具返回结果 |
| `status` | string | 执行状态：`success`、`error` |

### plan_created

Plan 模式下计划创建。

```json
{"type": "plan_created", "plan_id": "plan_001", "steps": ["步骤1", "步骤2"]}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `plan_id` | string | 计划 ID |
| `steps` | array[string] | 计划步骤列表 |

### plan_step_updated

计划步骤状态更新。

```json
{"type": "plan_step_updated", "step_id": "step_1", "status": "completed", "result": "已完成"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `step_id` | string | 步骤 ID |
| `status` | string | 状态：`pending`、`in_progress`、`completed`、`failed`、`skipped` |
| `result` | string | 步骤结果（可选） |

### ask_user

需要用户输入/确认。

```json
{
  "type": "ask_user",
  "question": "你想用哪个方案？",
  "options": [
    {"id": "a", "label": "方案 A"},
    {"id": "b", "label": "方案 B"}
  ],
  "questions": []
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `question` | string | 问题文本 |
| `options` | array | 选项列表，每项包含 `id` 和 `label` |
| `questions` | array | 多问题模式下的问题列表 |

用户回复通过 `POST /api/chat/answer` 或直接发新消息到同一 `conversation_id`。

### artifact

交付物（文件/图片/语音）。

```json
{
  "type": "artifact",
  "artifact_type": "image",
  "file_url": "https://...",
  "path": "/local/path",
  "name": "output.png",
  "caption": "生成的图片",
  "size": 102400
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `artifact_type` | string | 类型：`image`、`file`、`voice` |
| `file_url` | string | 文件访问 URL |
| `path` | string | 本地路径 |
| `name` | string | 文件名 |
| `caption` | string | 说明文字 |
| `size` | integer | 文件大小（字节） |

### agent_switch

Agent 配置切换。

```json
{"type": "agent_switch", "from": "default", "to": "code-assistant"}
```

### agent_handoff

Agent 交接（委派给子 Agent）。

```json
{"type": "agent_handoff", "target_agent": "browser-agent", "task_summary": "搜索最新新闻"}
```

### ui_preference

UI 偏好设置变更（由 `system_config` 工具触发）。

```json
{"type": "ui_preference", "theme": "dark", "language": "zh"}
```

### heartbeat

心跳事件，每 15 秒无数据时自动发送，防止连接超时。

```json
{"type": "heartbeat", "ts": 1711651200.0}
```

### error

错误事件。

```json
{"type": "error", "message": "Agent not initialized"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | string | 错误信息（最多 500 字符） |

### done

流结束事件，包含 token 用量统计。

```json
{
  "type": "done",
  "usage": {
    "input_tokens": 1500,
    "output_tokens": 800,
    "total_tokens": 2300,
    "context_tokens": 1800,
    "context_limit": 128000
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `usage` | object | 用量统计（可选） |
| `usage.input_tokens` | integer | 输入 token 数 |
| `usage.output_tokens` | integer | 输出 token 数 |
| `usage.total_tokens` | integer | 总 token 数 |
| `usage.context_tokens` | integer | 当前上下文 token 数 |
| `usage.context_limit` | integer | 上下文 token 上限 |

## 错误响应

### 409 Conflict — 会话忙

当同一 `conversation_id` 正在其他客户端处理中：

```json
{
  "error": "conversation_busy",
  "conversation_id": "conv_abc123",
  "busy_client_id": "tab_xyz",
  "busy_since": "2026-03-29T23:00:00",
  "message": "该会话正在其他终端进行中，请新建会话或稍后再试"
}
```

## 客户端断连处理

- 客户端断开后，服务端进入 **15 分钟宽限期**
- 宽限期内 Agent 任务仍继续执行
- 任务完成后结果自动保存到 session，用户刷新页面即可看到
- 宽限期超时后自动取消任务

## 相关接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/chat/busy` | GET | 查询当前忙的会话 |
| `/api/chat/answer` | POST | 回复 ask_user 事件 |
| `/api/chat/cancel` | POST | 取消当前任务 |
| `/api/chat/skip` | POST | 跳过当前步骤 |
| `/api/chat/insert` | POST | 插入用户消息到运行中任务 |
| `/api/agents/sub-tasks` | GET | 查询子 Agent 实时状态 |
| `/api/agents/sub-records` | GET | 查询子 Agent 历史记录 |
