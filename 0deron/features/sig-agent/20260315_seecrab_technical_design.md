# SeeCrab 技术详细设计文档

> **项目**: OpenAkita SeeCrab WebApp
> **版本**: v1.0
> **日期**: 2026-03-15
> **作者**: 架构设计
> **状态**: 待评审

---

## 1. 概述

### 1.1 项目定位

SeeCrab 是 OpenAkita 的独立前端 Web 应用（`apps/seecrab/`），提供 Agent 执行过程的实时可视化界面。覆盖三个场景：

| 场景 | 说明 |
|------|------|
| 普通模式 (Normal) | 简单问答、0~2 个工具调用，三段式结构 |
| 计划模式 (Plan) | 多步复杂任务，四段式结构（含 Plan Checklist）|
| 多 Agent 模式 | Orchestrator 协调多个专家 Agent 并行执行 |

### 1.2 设计原则

- **后端负责智能，前端负责展示**：过滤、聚合、计时、标题生成全部在后端 Adapter 层完成
- **统一消息协议**：三种场景共享同一套 SSE 事件类型，前端根据事件内容自适应渲染
- **高内聚低耦合**：后端 Adapter 由 5 个职责单一的子模块组成，前端 Vue 组件按功能分层
- **不修改现有核心**：Agent、ReasoningEngine、Brain、ToolExecutor 等核心模块零改动

### 1.3 技术选型

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端框架 | Vue 3 + TypeScript | 独立于 setup-center 的前端应用 |
| 构建工具 | Vite | 开发热重载 + 生产构建 |
| 状态管理 | Pinia | Vue 3 官方推荐 |
| 通信协议 | SSE (Server-Sent Events) | 单向流式推送，复用现有基础设施 |
| 后端框架 | FastAPI (现有) | 新增 SeeCrab 专用路由 |
| Adapter 层 | Python asyncio | 异步事件流翻译 |

---

## 2. 系统架构

### 2.1 整体分层

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SeeCrab 前端 (Vue 3 + Vite)                         │
│  apps/seecrab/                                                              │
│                                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │ SSE      │  │ Chat     │  │ StepCard │  │ Detail   │  │ Timer    │     │
│  │ Client   │→ │ Store    │→ │ List     │  │ Panel    │  │ Display  │     │
│  │          │  │ (Pinia)  │  │          │  │          │  │          │     │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
│       ↑                                                                     │
│       │ SSE (text/event-stream)                                             │
│       │ 精炼后的 7+3 种事件类型                                               │
├───────┼─────────────────────────────────────────────────────────────────────┤
│       │                                                                     │
│       │         SeeCrab 后端 (FastAPI 新增路由)                               │
│       │         src/openakita/api/routes/seecrab.py                         │
│       │                                                                     │
│  ┌────┴─────────────────────────────────────────────────────────────────┐   │
│  │                    SeeCrabAdapter (核心翻译层)                        │   │
│  │         src/openakita/api/adapters/seecrab_adapter.py                │   │
│  │                                                                     │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                │   │
│  │  │ StepFilter  │  │ StepAggre-  │  │ TimerTracker │                │   │
│  │  │ (过滤决策)   │  │ gator       │  │ (TTFT/Total  │                │   │
│  │  │             │  │ (聚合状态机)  │  │  计时)       │                │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                │   │
│  │  ┌─────────────┐  ┌─────────────┐                                  │   │
│  │  │ TitleGen    │  │ CardBuilder │                                  │   │
│  │  │ (LLM标题    │  │ (卡片组装)   │                                  │   │
│  │  │  生成)      │  │             │                                  │   │
│  │  └─────────────┘  └─────────────┘                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       ↑                                                                     │
│       │ 原始事件流 (reason_stream)                                           │
│  ┌────┴─────────────────────────────────────────────────────────────────┐   │
│  │              现有 Agent 核心 (不修改)                                 │   │
│  │  Agent → ReasoningEngine → ToolExecutor → Brain                     │   │
│  │  Sessions / Memory / Skills / MCP / Channels                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
单 Agent:
  用户输入 → POST /api/seecrab/chat → Agent.chat_with_session_stream()
           → SeeCrabAdapter.transform() → SSE 精炼事件 → Vue 前端渲染

多 Agent:
  Orchestrator → Agent1.stream() ─┐
                 Agent2.stream() ─┼→ MultiAgentAdapter 汇聚 → SSE → 前端
                 Agent3.stream() ─┘
```

---

## 3. SSE 统一事件协议

### 3.1 事件类型定义

SeeCrabAdapter 输出 7 种核心事件 + 3 种控制事件：

| 事件类型 | 方向 | 说明 | 适用场景 |
|---------|------|------|---------|
| `thinking` | 后端→前端 | Agent 推理过程（流式 delta） | 全场景 |
| `plan_checklist` | 后端→前端 | 计划清单（创建/更新），含步骤列表和状态 | Plan Mode |
| `step_card` | 后端→前端 | 步骤卡片（创建/更新），含标题、状态、I/O | 全场景 |
| `ai_text` | 后端→前端 | 最终文本输出（流式 delta） | 全场景 |
| `ask_user` | 后端→前端 | 人机交互确认（问题+选项） | 全场景 |
| `agent_header` | 后端→前端 | Agent 回复头（agent_id, name, avatar） | 多 Agent |
| `artifact` | 后端→前端 | 文件/结果产物（deliver_artifacts） | 全场景 |
| `timer_update` | 后端→前端 | 计时器更新（TTFT/Total） | 全场景 |
| `heartbeat` | 后端→前端 | 保活信号 | 全场景 |
| `done` | 后端→前端 | 流结束信号 | 全场景 |

### 3.2 事件体结构

```json
// thinking
{
  "type": "thinking",
  "content": "用户想了解 Karpathy 的最新观点，我需要先搜索...",
  "agent_id": "main"
}

// plan_checklist
{
  "type": "plan_checklist",
  "steps": [
    {"index": 1, "title": "搜索 Karpathy 最新观点", "status": "pending"},
    {"index": 2, "title": "整理要点并归类", "status": "pending"},
    {"index": 3, "title": "生成摘要报告", "status": "pending"}
  ]
}

// step_card
{
  "type": "step_card",
  "step_id": "step_001",
  "title": "搜索 Karpathy 2026 最新观点",
  "status": "completed",          // running | completed | failed
  "source_type": "skill",         // tool | skill | mcp | plan_step
  "card_type": "search",          // search | code | file | analysis | browser | default
  "duration": 3.2,
  "plan_step_index": null,        // Plan 模式关联
  "agent_id": "main",
  "input": {"query": "Karpathy 2026", "max_results": 5},
  "output": "搜索结果: 1. Karpathy 在...",
  "absorbed_calls": [
    {"tool": "web_search", "tool_id": "t1", "args": {"query": "karpathy 2026 blog"}, "duration": 1.1},
    {"tool": "web_search", "tool_id": "t2", "args": {"query": "karpathy AI agent"}, "duration": 1.5},
    {"tool": "read_file", "tool_id": "t3", "args": {"path": "result.json"}, "duration": 0.2, "is_error": false}
  ]
}

// ai_text
{
  "type": "ai_text",
  "content": "根据搜索结果...",
  "agent_id": "main"
}

// ask_user
{
  "type": "ask_user",
  "ask_id": "ask_001",
  "question": "您想要哪种格式的报告？",
  "options": [
    {"label": "PDF", "value": "pdf"},
    {"label": "Markdown", "value": "md"}
  ]
}

// agent_header
{
  "type": "agent_header",
  "agent_id": "agent_1",
  "name": "搜索专家",
  "role": "researcher",
  "avatar": null
}

// artifact
{
  "type": "artifact",
  "artifact_type": "file",
  "file_url": "/api/files/report.pdf",
  "filename": "report.pdf",
  "mime_type": "application/pdf"
}

// timer_update
{
  "type": "timer_update",
  "reply_id": "reply_abc123",
  "phase": "ttft",              // ttft | total
  "state": "done",              // running | done | cancelled
  "value": 1.2,                 // 秒，仅 done/cancelled 时有
  "server_ts": 1710500000.123
}

// error
{
  "type": "error",
  "message": "Agent execution failed: ...",
  "code": "agent_error"            // agent_error | timeout | internal
}

// done
{
  "type": "done"
}
```

> **字段映射说明**：
> - `plan_created` 的 `steps` 使用 `{index, title}` 格式。原始引擎事件中 `plan.steps` 使用 `{id, description, status}`，Adapter 需将 `id` 映射为递增 `index`，`description` 映射为 `title`，并维护 `id→index` 反查字典。
> - `plan_step_updated` 中引擎发送 `stepId`（字符串如 `"step_1"`），Adapter 通过反查字典转换为 `step_index`（数字）。
> - `ask_user.options` 使用 `{label, value}` 格式。原始事件中 `ask_user` 的 options 使用 `{id, label}` 字段，Adapter 需将 `id` 映射为 `value`。`ask_id` 用于前端关联回答。
> - `artifact` 事件由 Adapter 从 `deliver_artifacts` 工具的 `tool_call_end` 结果中合成，原始事件流中不存在独立的 `artifact` 事件类型。
> - `absorbed_calls` 每项包含 `tool_id`（关联 tool_call_end）和可选的 `is_error` 标志。

### 3.3 事件序列示例

#### 普通模式 (Normal Mode)

```
→ timer_update  { reply_id, phase:"ttft",  state:"running" }
→ thinking      { content: "用户想了解..." }
→ timer_update  { reply_id, phase:"ttft",  state:"done", value:0.8 }
→ timer_update  { reply_id, phase:"total", state:"running" }
→ step_card     { step_id, status:"running", title:"搜索 Karpathy 最新观点" }
→ step_card     { step_id, status:"completed", duration:3.2, input:{...}, output:"..." }
→ ai_text       { content: "根据搜索结果..." }
→ ai_text       { content: "Karpathy 最近..." }
→ timer_update  { reply_id, phase:"total", state:"done", value:4.8 }
→ done          {}
```

#### 计划模式 (Plan Mode)

```
→ timer_update  { reply_id, phase:"ttft",  state:"running" }
→ thinking      { content: "这个任务需要..." }
→ timer_update  { reply_id, phase:"ttft",  state:"done", value:0.9 }
→ timer_update  { reply_id, phase:"total", state:"running" }
→ plan_checklist { steps: [{idx:1,title:"搜索",status:"pending"}, ...] }
→ step_card     { step_id, plan_step_index:1, status:"running", title:"搜索 Karpathy" }
→ plan_checklist { steps: [{idx:1,status:"running"}, ...] }
→ step_card     { step_id, plan_step_index:1, status:"completed", ... }
→ plan_checklist { steps: [{idx:1,status:"completed"}, ...] }
→ step_card     { step_id, plan_step_index:2, status:"running", ... }
→ ...
→ ai_text       { content: "总结..." }
→ timer_update  { reply_id, phase:"total", state:"done", value:12.3 }
→ done          {}
```

#### 多 Agent 模式

```
→ agent_header  { agent_id:"main", name:"协调者", role:"orchestrator" }
→ thinking      { agent_id:"main", content: "需要并行调研..." }
→ agent_header  { agent_id:"agent_1", name:"搜索专家", role:"researcher" }
→ step_card     { agent_id:"agent_1", step_id, status:"running", ... }
→ agent_header  { agent_id:"agent_2", name:"数据分析师", role:"analyst" }
→ step_card     { agent_id:"agent_2", step_id, status:"running", ... }
→ step_card     { agent_id:"agent_1", step_id, status:"completed", ... }
→ step_card     { agent_id:"agent_2", step_id, status:"completed", ... }
→ agent_header  { agent_id:"main", name:"协调者" }
→ ai_text       { agent_id:"main", content: "综合各方调研结果..." }
→ done          {}
```

---

## 4. 步骤卡片过滤与聚合

### 4.1 设计原则

步骤卡片的目标是向用户展示**有业务语义的执行进展**，而非 Agent 内部的技术调用细节。

| 原则 | 说明 |
|------|------|
| 用户视角 | 只展示用户能理解且关心的操作 |
| 语义聚合 | 多次底层调用聚合为一个有意义的步骤 |
| 模式感知 | 普通模式和计划模式有不同的聚合策略 |
| 体验优先 | 卡片标题需要语义化，体现用户意图而非工具名 |

### 4.2 聚合状态机

StepAggregator 维护一个三状态状态机：

```
状态:
  IDLE          — 默认空闲，无活跃聚合上下文
  SKILL_ABSORB  — Skill 吸收中，所有子调用被吸收到 Skill 卡片
  MCP_ABSORB    — MCP 吸收中，同 Server 的连续调用被吸收
  PLAN_ABSORB   — Plan 步骤执行中，所有调用被吸收到 Plan Step 卡片

转移条件:

  IDLE → SKILL_ABSORB:
    触发: load_skill / run_skill_script 的 tool_call_start
    注: get_skill_info 为只读查询，不触发 SKILL_ABSORB（归为 HIDDEN）
    动作: 创建 pending Skill 卡片，启动 LLM 标题生成

  SKILL_ABSORB → IDLE:
    触发: text_delta / done / 新的 load_skill
    动作: 完成当前 Skill 卡片

  SKILL_ABSORB (内部):
    任何其他 tool_call → 吸收到当前 Skill 卡片的 absorbed_calls

  IDLE → MCP_ABSORB(server=X):
    触发: call_mcp_tool(server=X) 的 tool_call_start
    动作: 创建 pending MCP 卡片，启动 LLM 标题生成

  MCP_ABSORB(X) → IDLE:
    触发: 非 call_mcp_tool 的工具调用 / text_delta / done
    动作: 完成当前 MCP 卡片

  MCP_ABSORB(X) → MCP_ABSORB(Y):
    触发: call_mcp_tool(server=Y, Y≠X)
    动作: 完成当前 MCP 卡片，创建新 MCP 卡片

  MCP_ABSORB(X) (内部):
    call_mcp_tool(server=X) → 吸收到当前 MCP 卡片

  * → PLAN_ABSORB:
    触发: plan_created 事件
    动作: 进入 Plan 全吸收模式

  PLAN_ABSORB (内部):
    所有 tool_call → 吸收到当前 Plan Step 卡片
    plan_step_updated(completed) → 完成当前步骤卡片，准备下一步

  PLAN_ABSORB → IDLE:
    触发: plan_completed 事件

  注意: PLAN_ABSORB 期间不维护 SKILL_ABSORB / MCP_ABSORB 子状态。
  Plan 模式下所有工具调用（包括 Skill 和 MCP）直接吸收到 Plan Step 卡片，
  不触发任何子聚合逻辑。状态机在 PLAN_ABSORB 时忽略 Skill/MCP 触发信号。
```

### 4.3 完整过滤决策流程

```
tool_call_start 事件到达
      │
      ▼
┌─────────────────────────┐
│ Plan 模式且在步骤执行中？ │── 是 ──→ 吸收到当前 plan_step 卡片
│                         │         absorbed_calls 追加记录
└──────────┬──────────────┘         不触发任何子聚合
           │ 否
           ▼
┌─────────────────────────┐
│ 当前处于 SKILL_ABSORB？  │── 是 ──→ 吸收到 Skill 卡片
│                         │         absorbed_calls 追加记录
└──────────┬──────────────┘
           │ 否
           ▼
┌─────────────────────────┐
│ 当前处于 MCP_ABSORB     │
│ 且 server 相同？        │── 是 ──→ 吸收到 MCP 卡片
└──────────┬──────────────┘         absorbed_calls 追加记录
           │ 否
           ▼
┌─────────────────────────┐
│ MCP_ABSORB 但 server    │── 是 ──→ 完成当前 MCP 卡片
│ 不同？                  │         创建新 MCP 卡片 → MCP_ABSORB(new)
└──────────┬──────────────┘
           │ 否 (IDLE 状态)
           ▼
┌─────────────────────────┐
│ 是 Skill 触发调用？      │── 是 ──→ 创建 pending Skill 卡片
│ (load_skill /           │         启动 LLM 标题生成
│  run_skill_script)      │         → SKILL_ABSORB
└──────────┬──────────────┘
           │ 否
           ▼
┌─────────────────────────┐
│ 是 call_mcp_tool？      │── 是 ──→ 创建 pending MCP 卡片
│                         │         启动 LLM 标题生成
└──────────┬──────────────┘         → MCP_ABSORB(server)
           │ 否
           ▼
┌─────────────────────────┐
│ 工具在白名单中？         │── 是 ──→ 创建独立 step_card (status=running)
│                         │         标题 = HUMANIZE_MAP[tool] + args
│                         │         注册 tool_id→step_id 到 _independent_cards
│                         │         → tool_call_end 时自动完成(completed/failed)
└──────────┬──────────────┘
           │ 否
           ▼
┌─────────────────────────┐
│ 用户明确提及？           │── 是 ──→ 提升为可见 step_card
│ (关键词匹配)             │         仅普通模式生效
└──────────┬──────────────┘
           │ 否
           ▼
    丢弃，不生成卡片
```

### 4.4 白名单工具

以下工具的调用直接生成独立步骤卡片（标题使用 humanize 映射，无需 LLM）：

| 工具名 | 业务语义 | 标题生成规则 |
|--------|---------|-------------|
| `web_search` | 网络搜索 | `搜索 "{query}"` |
| `news_search` | 新闻搜索 | `搜索新闻 "{query}"` |
| `browser_task` | 浏览网页 | `浏览网页获取内容` |
| `generate_image` | 生成图片 | `生成插图` |
| `deliver_artifacts` | 发送文件 | `发送 {filename}` |
| `delegate_to_agent` | 委派子代理 | `委派专家代理处理` |
| `delegate_parallel` | 并行委派 | `并行调研多个方向` |

白名单可配置（`StepFilterConfig.whitelist`），后续可按需增减。

### 4.5 Skill / MCP 聚合边界检测

#### Skill 聚合

| 边界 | 信号 | 说明 |
|------|------|------|
| 开始 | `load_skill` / `run_skill_script` 的 `tool_call_start` | 创建 pending 卡片（`get_skill_info` 为只读查询，不触发聚合） |
| 结束 | `text_delta` 事件 | Agent 开始输出文本，Skill 执行完毕 |
| 结束 | `done` 事件 | 流结束 |
| 结束 | 新的 `load_skill` | 另一个 Skill 开始 |
| 结束 | 超过 N 个迭代无 tool_call | 超时兜底 |

> **注意**: `tool_call_end` 的 `load_skill` 不是结束信号（Skill 的工具可能还在继续调用）

#### MCP 聚合

| 边界 | 信号 | 说明 |
|------|------|------|
| 开始 | `call_mcp_tool(server=X)` 的 `tool_call_start` | 创建 pending 卡片 |
| 结束 | 下一个 `tool_call_start` 不是同一 MCP Server | 聚合中断 |
| 结束 | `text_delta` / `done` | 流结束或 Agent 输出 |

#### Plan 模式

| 边界 | 信号 | 说明 |
|------|------|------|
| 进入 | `plan_created` | 进入 Plan 全吸收模式 |
| 步骤开始 | `plan_step_updated(status:"running")` | 当前步骤的工具调用开始吸收 |
| 步骤结束 | `plan_step_updated(status:"completed")` | 完成当前步骤卡片 |
| 退出 | `plan_completed` | 退出 Plan 模式 |

### 4.6 优先级与嵌套

```
优先级（高 → 低）: Plan Step > Skill > MCP > 白名单工具
```

| 嵌套场景 | 处理方式 |
|---------|---------|
| Plan 内 Skill/MCP | 全部吸收到 Plan Step（Plan 最高优先级） |
| Skill 内 MCP | 吸收到 Skill（Skill > MCP） |
| Skill 内白名单 | 吸收到 Skill |
| MCP 连续中穿插白名单 | MCP 聚合中断，白名单独立生成卡片 |
| 并行的多个白名单工具 | 各自独立生成步骤卡片 |
| 并行的多个 MCP (同 Server) | 聚合为一张卡片 |
| 并行的多个 MCP (不同 Server) | 各自独立生成卡片 |

### 4.7 LLM 标题生成

Skill 和 MCP 的步骤卡片标题**必须由 LLM 生成**（`brain.think_lightweight`）。

#### 生成流程

```
Skill / MCP 触发
      │
      ▼
创建 pending 卡片 ──→ 立即下发 step_card(status:"running", title:"⏳")
      │                前端显示 loading 占位标题
      │
      ▼
异步调用 brain.think_lightweight()
      │
      ├── 成功 (≤30s) ──→ 更新 step_card(title: "LLM 生成的标题")
      │
      └── 失败/超时 ──→ 降级标题
```

#### Prompt 模板

```
根据以下信息，生成一个简短、对用户友好的步骤标题：

用户最近消息：
{recent_messages}

正在执行的技能/服务：
- 名称：{name}
- 描述：{description}
- 分类：{category}

要求：
- 使用动词开头（如"搜索"、"分析"、"生成"、"整理"）
- 体现用户意图，而非技术操作名称
- 简洁明了，不超过 15 个字
- 使用用户的语言（中文/英文跟随用户消息）
```

#### 降级策略

| 异常场景 | 降级标题 |
|---------|---------|
| LLM 调用超时 (30s) | Skill: `"{skill_name}: {description[:15]}"`; MCP: `"调用 {server_name} 服务"` |
| LLM 返回空/无效 | 同上 |
| Skill/MCP 无 meta | 使用工具名（如 `"调用 github 服务"`） |
| Brain 未初始化 | 跳过 LLM，直接用降级标题 |

#### 并发控制

- 同时最多 3 个标题生成任务，超出排队
- 使用 `asyncio.Semaphore(3)` 控制

### 4.8 用户明确提及的隐藏工具

当用户消息中明确提到了某个隐藏工具的操作时，该工具的隐藏规则被覆盖（仅普通模式生效）：

| 用户消息 | 触发工具 | 处理方式 |
|---------|---------|---------|
| "帮我读取 config.yaml" | `read_file` | 提升为可见步骤卡片 |
| "运行 npm install" | `run_shell` | 提升为可见步骤卡片 |
| "写一个 README.md 文件" | `write_file` | 提升为可见步骤卡片 |

检测方式：对用户最近消息进行关键词/意图匹配。配置在 `StepFilterConfig.user_mention_keywords`。

---

## 5. 计时器处理逻辑

### 5.1 后端 TimerTracker

#### 数据模型

```python
@dataclass
class ReplyTimer:
    reply_id: str
    t_request: float        # 用户请求到达时间 (time.monotonic)
    t_first_token: float | None = None
    t_done: float | None = None
    step_timers: dict[str, StepTimer] = field(default_factory=dict)

@dataclass
class StepTimer:
    step_id: str
    t_start: float
    t_end: float | None = None
```

#### 采集时机

| 计时器 | 开始 | 触发 | 计算 |
|--------|------|------|------|
| TTFT | SeeCrabAdapter 收到第一个事件时记录 `t_request` | 首个 `thinking_delta` 或 `text_delta` | `t_first_token - t_request` |
| Total | 同 TTFT 的 `t_request` | `done` 事件 | `t_done - t_request` |
| Step Duration | `step_card(running)` 发出时 | `step_card(completed/failed)` 发出时 | `t_end - t_start` |

#### 精度

- 使用 `time.monotonic()`，不受系统时钟调整影响
- 精确到毫秒，SSE 事件中显示保留 1 位小数（秒）

#### 特殊场景

- Agent 被取消（用户断开）：使用取消时间作为 `t_done`，phase 标记为 `"cancelled"`
- 聚合卡片的 duration：从第一个子调用开始到最后一个子调用结束

### 5.2 timer_update 事件结构

```json
{
  "type": "timer_update",
  "reply_id": "reply_abc123",
  "phase": "ttft",              // ttft | total
  "state": "running",           // running | done | cancelled
  "value": null,                // 精确值(秒)，仅 done/cancelled 时有
  "server_ts": 1710500000.123   // 服务器时间戳(诊断用)
}
```

### 5.3 前端 TimerDisplay

#### 运行态 — 蓝色脉冲 + 本地递增

收到 `timer_update(state:"running")` 后：
1. 记录本地时间 `localStartTime = performance.now()`
2. 启动 `requestAnimationFrame` 循环，每帧更新显示值
3. `displayValue = (performance.now() - localStartTime) / 1000`
4. 蓝色脉冲动画：`CSS animation: pulse 1.5s ease-in-out infinite`

#### 完成态 — 灰色静态

收到 `timer_update(state:"done", value:1.2)` 后：
1. 停止 rAF 递增
2. 用后端精确值 `value` 替换本地递增值
3. 圆点变灰色静态，数字固定

#### 校准策略

- **为什么需要本地递增？** SSE 事件有传输延迟，只依赖后端推送会导致计时器"卡顿"
- **校准时机**: 收到 `state:"done"` → 用后端精确值一次性替换
- **误差范围**: 正常情况 <200ms（网络 RTT），用户无感
- **防回退**: 如果本地值 > 后端值，直接用后端值（不做回退动画）

---

## 6. 后端模块详细设计

### 6.1 文件结构

```
src/openakita/api/
├── routes/
│   └── seecrab.py                  # SeeCrab 专用路由
├── adapters/
│   ├── seecrab_adapter.py          # 核心翻译层(调度器)
│   ├── step_filter.py              # 过滤决策 (替代 tool_filter.py)
│   ├── step_aggregator.py          # 聚合状态机
│   ├── timer_tracker.py            # 计时器采集
│   ├── title_generator.py          # LLM 标题生成
│   ├── card_builder.py             # 卡片组装 (替代 card_type_mapper.py)
│   ├── multi_agent_adapter.py      # 多 Agent 流汇聚
│   ├── tool_filter.py              # 保留(旧代码兼容)
│   └── card_type_mapper.py         # 保留(旧代码兼容)
└── schemas/
    └── seecrab.py                  # 请求/响应 schema
```

### 6.2 seecrab.py — 路由

```python
# API 端点
POST /api/seecrab/chat              # SSE 流式聊天
GET  /api/seecrab/sessions          # 会话列表
GET  /api/seecrab/sessions/{id}     # 会话详情(含历史消息+step_cards)
POST /api/seecrab/sessions          # 创建新会话
POST /api/seecrab/answer            # ask_user 回答（返回 hint，客户端需发新 chat 消息）
```

- 复用现有 busy-lock 机制（600s TTL，threading.Lock 线程安全清理）
- Agent 获取：优先从 AgentInstancePool 按 conversation_id 获取独立实例，fallback 到全局 agent
- 复用现有 Agent 池 / Session 管理
- SSE 响应使用 `StreamingResponse(media_type="text/event-stream")`

### 6.3 seecrab_adapter.py — 核心翻译层

```python
class SeeCrabAdapter:
    """原始事件流 → 精炼 SSE 事件的翻译层"""

    def __init__(self, brain: Brain, user_messages: list[str]):
        self.step_filter = StepFilter()
        self.timer = TimerTracker()
        self.title_gen = TitleGenerator(brain, user_messages)
        self.card_builder = CardBuilder()
        self._title_queue: asyncio.Queue[dict] = asyncio.Queue()
        self.aggregator = StepAggregator(
            title_gen=self.title_gen,
            card_builder=self.card_builder,
            timer=self.timer,
            title_update_queue=self._title_queue,
        )

    async def transform(
        self,
        raw_events: AsyncIterator[dict],
        reply_id: str,
    ) -> AsyncIterator[dict]:
        """消费原始事件 + title_update_queue，产出精炼事件"""
        self.timer.start(reply_id)
        yield self.timer.make_event("ttft", "running")

        async for event in raw_events:
            refined = await self._process_event(event)
            for e in refined:
                yield e
            # 在每个原始事件之间排空标题更新队列
            while not self._title_queue.empty():
                try:
                    yield self._title_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

        # Flush 聚合状态
        for e in await self.aggregator.flush():
            yield e

        # 排空剩余标题更新
        while not self._title_queue.empty():
            try:
                yield self._title_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # 流结束
        yield self.timer.make_event("total", "done")
        yield {"type": "done"}
```

核心方法 `_process_event` 按事件类型分发：
- `thinking_start/delta/end` → 转换为 `thinking` 事件 + TTFT 检测
- `text_delta` → 转换为 `ai_text` 事件 + TTFT 检测 + Skill 聚合边界检测
- `tool_call_start` → 交给 `StepFilter` + `StepAggregator` 处理
- `tool_call_end` → 完成独立卡片（白名单工具） / 更新 absorbed_call 结果；若为 `deliver_artifacts` 则合成 `artifact` 事件
- `plan_created` → 字段映射（`id`→`index`, `description`→`title`），建立 `_plan_id_to_index` 反查字典
- `plan_step_updated` → 将引擎的 `stepId`（字符串）通过反查字典转为数字 `step_index`
- `plan_completed` → 退出 Plan 模式
- `ask_user` → 转发为 `ask_user` 事件（`id`→`value`, 增加 `ask_id` 用于关联回答）
- `heartbeat` → 转发
- `done` → 清理 + 最终计时
- `error` → 转发为 `error` 事件
- 以下引擎事件显式丢弃（不与前端相关）：`done`（引擎信号，Adapter 自行发出）、`iteration_start`、`context_compressed`、`chain_text`、`user_insert`、`agent_handoff`、`tool_call_skipped`

### 6.4 step_filter.py — 过滤决策

```python
@dataclass
class StepFilterConfig:
    whitelist: list[str] = field(default_factory=lambda: [
        "web_search", "news_search", "browser_task",
        "generate_image", "deliver_artifacts",
        "delegate_to_agent", "delegate_parallel",
    ])
    skill_triggers: list[str] = field(default_factory=lambda: [
        "load_skill", "run_skill_script",
    ])
    mcp_trigger: str = "call_mcp_tool"
    user_mention_keywords: dict[str, list[str]] = field(default_factory=lambda: {
        "read_file": ["读取", "读", "查看文件", "打开文件"],
        "write_file": ["写入", "写", "创建文件", "生成文件"],
        "run_shell": ["运行", "执行", "跑"],
    })

class StepFilter:
    def __init__(self, config: StepFilterConfig | None = None):
        self.config = config or StepFilterConfig()

    def classify(self, tool_name: str, args: dict) -> FilterResult:
        """分类工具调用: skill_trigger / mcp_trigger / whitelist / user_mention / hidden"""
        ...
```

### 6.5 step_aggregator.py — 聚合状态机

```python
class StepAggregator:
    state: AggregatorState = IDLE
    pending_card: PendingCard | None = None
    _independent_cards: dict[str, str] = {}  # tool_id → step_id (白名单卡片追踪)
    _plan_id_to_index: dict[str, int] = {}   # engine "step_1" → numeric 1 (Plan 字段映射)
    _title_update_queue: asyncio.Queue | None  # 异步标题更新队列

    async def on_tool_call_start(
        self, tool_name: str, args: dict, tool_id: str,
        filter_result: FilterResult
    ) -> list[dict]:
        """处理 tool_call_start，返回需要发送的事件列表"""
        ...

    async def on_tool_call_end(
        self, tool_name: str, tool_id: str, result: str, is_error: bool
    ) -> list[dict]:
        """处理 tool_call_end:
        - 独立卡片 → 完成并返回 completed/failed step_card
        - 聚合模式 → 更新 absorbed_call 结果
        """
        ...

    async def on_text_delta(self) -> list[dict]:
        """text_delta 到达，检查是否需要关闭聚合"""
        ...

    async def on_plan_created(self, plan: dict) -> list[dict]:
        """进入 Plan 模式
        字段映射: steps[].id→index, steps[].description→title
        维护 _plan_id_to_index 反查字典
        """
        ...

    async def on_plan_step_updated(self, step_index: int, status: str) -> list[dict]:
        """Plan 步骤状态更新 (step_index 已由 Adapter 从 stepId 转换)"""
        ...
```

### 6.6 timer_tracker.py — 计时器采集

```python
class TimerTracker:
    reply_timer: ReplyTimer | None = None
    ttft_triggered: bool = False

    def start(self, reply_id: str):
        self.reply_timer = ReplyTimer(reply_id=reply_id, t_request=time.monotonic())

    def check_ttft(self) -> dict | None:
        """检查是否为首 token，返回 timer_update 事件或 None"""
        if not self.ttft_triggered:
            self.ttft_triggered = True
            self.reply_timer.t_first_token = time.monotonic()
            return self.make_event("ttft", "done")
        return None

    def start_step(self, step_id: str):
        self.reply_timer.step_timers[step_id] = StepTimer(
            step_id=step_id, t_start=time.monotonic()
        )

    def end_step(self, step_id: str) -> float:
        timer = self.reply_timer.step_timers[step_id]
        timer.t_end = time.monotonic()
        return round(timer.t_end - timer.t_start, 1)

    def make_event(self, phase: str, state: str) -> dict:
        value = None
        if state == "done":
            if phase == "ttft":
                value = round(self.reply_timer.t_first_token - self.reply_timer.t_request, 1)
            elif phase == "total":
                self.reply_timer.t_done = time.monotonic()
                value = round(self.reply_timer.t_done - self.reply_timer.t_request, 1)
        return {
            "type": "timer_update",
            "reply_id": self.reply_timer.reply_id,
            "phase": phase,
            "state": state,
            "value": value,
            "server_ts": time.time(),
        }
```

### 6.7 title_generator.py — LLM 标题生成

```python
class TitleGenerator:
    TITLE_TIMEOUT = 30  # 秒
    MAX_CONCURRENT = 3

    def __init__(self, brain: Brain, user_messages: list[str]):
        self.brain = brain
        self.user_messages = user_messages[-5:]  # 最近 5 条
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)

    async def generate_skill_title(self, skill_meta: dict) -> str:
        """为 Skill 生成 LLM 标题"""
        ...

    async def generate_mcp_title(self, server_meta: dict, tool_meta: dict) -> str:
        """为 MCP 工具生成 LLM 标题"""
        ...

    def humanize_tool_title(self, tool_name: str, args: dict) -> str:
        """白名单工具的 humanize 标题（无 LLM）"""
        ...

    # HUMANIZE_MAP 映射表
    HUMANIZE_MAP = {
        "web_search": lambda args: f'搜索 "{args.get("query", "")}"',
        "news_search": lambda args: f'搜索新闻 "{args.get("query", "")}"',
        "browser_task": lambda args: "浏览网页获取内容",
        "generate_image": lambda args: "生成插图",
        "deliver_artifacts": lambda args: f'发送 {args.get("filename", "文件")}',
        "delegate_to_agent": lambda args: "委派专家代理处理",
        "delegate_parallel": lambda args: "并行调研多个方向",
    }
```

### 6.8 card_builder.py — 卡片组装

```python
class CardBuilder:
    CARD_TYPE_MAP = {
        "web_search": "search", "news_search": "search", "search_*": "search",
        "code_execute": "code", "python_execute": "code", "shell_execute": "code",
        "generate_report": "file", "deliver_artifacts": "file", "export_*": "file",
        "analyze_data": "analysis", "chart_*": "analysis",
        "browser_*": "browser", "navigate_*": "browser",
    }

    def build_step_card(
        self, step_id: str, title: str, status: str,
        source_type: str, tool_name: str,
        plan_step_index: int | None = None,
        agent_id: str = "main",
        duration: float | None = None,
        input_data: dict | None = None,
        output_data: str | None = None,
        absorbed_calls: list[dict] | None = None,
    ) -> dict:
        """组装完整的 step_card 事件"""
        return {
            "type": "step_card",
            "step_id": step_id,
            "title": title,
            "status": status,
            "source_type": source_type,
            "card_type": self._get_card_type(tool_name),
            "duration": duration,
            "plan_step_index": plan_step_index,
            "agent_id": agent_id,
            "input": input_data,
            "output": output_data,
            "absorbed_calls": absorbed_calls or [],
        }
```

### 6.9 multi_agent_adapter.py — 多 Agent 流汇聚

```python
class MultiAgentAdapter:
    """将多个 Agent 的事件流合并为单一 SSE 流"""

    async def merge_streams(
        self,
        agent_streams: dict[str, tuple[AgentInfo, AsyncIterator[dict]]],
        brain: Brain,
        reply_id: str,
    ) -> AsyncIterator[dict]:
        """合并多个 Agent 的精炼事件流

        每个 Agent 有独立的 SeeCrabAdapter 实例。
        使用 asyncio.Queue 合并，保证事件顺序。
        在 Agent 切换时注入 agent_header 事件。

        排序保证：
        - 每个 Agent 的事件内部保持原始顺序
        - 不同 Agent 的事件按到达 Queue 的时间交错排列
        - 同一时刻到达的事件按 agent_id 字典序排列（确定性）

        错误隔离：
        - 单个 Agent 流的异常不影响其他 Agent
        - 异常 Agent 的流标记为 error，其余继续
        - Orchestrator 流（agent_id="main"）异常则整体终止

        Orchestrator 事件穿插：
        - Orchestrator 的 thinking / ai_text 事件在所有子 Agent 事件之间自然交错
        - Orchestrator 发起 delegate_to_agent 时注入 agent_header 标记切换
        """
        ...
```

---

## 7. 前端模块详细设计

### 7.1 文件结构

```
apps/seecrab/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── api/
│   │   ├── sse-client.ts           # SSE 连接管理
│   │   └── http-client.ts          # REST API
│   ├── stores/
│   │   ├── chat.ts                 # 聊天状态 (Pinia)
│   │   ├── session.ts              # 会话管理
│   │   └── ui.ts                   # UI 状态
│   ├── composables/
│   │   ├── useTimer.ts             # 计时器展示
│   │   ├── useMarkdown.ts          # Markdown 渲染
│   │   └── useAutoScroll.ts        # 自动滚动
│   ├── components/
│   │   ├── layout/
│   │   │   ├── LeftSidebar.vue
│   │   │   ├── ChatArea.vue
│   │   │   └── RightPanel.vue
│   │   ├── chat/
│   │   │   ├── MessageList.vue
│   │   │   ├── UserMessage.vue
│   │   │   ├── BotReply.vue
│   │   │   ├── ReplyHeader.vue
│   │   │   ├── ThinkingBlock.vue
│   │   │   ├── PlanChecklist.vue
│   │   │   ├── StepCardList.vue
│   │   │   ├── StepCard.vue
│   │   │   ├── SummaryOutput.vue
│   │   │   ├── AskUserBlock.vue
│   │   │   └── ChatInput.vue
│   │   ├── welcome/
│   │   │   └── WelcomePage.vue
│   │   └── detail/
│   │       ├── StepDetail.vue
│   │       ├── InputViewer.vue
│   │       └── OutputViewer.vue
│   ├── types/
│   │   └── index.ts
│   └── styles/
│       └── main.css
└── public/
    └── favicon.svg
```

### 7.2 SSE 客户端 (sse-client.ts)

> **实现说明**：由于聊天端点是 POST 请求，无法使用浏览器原生 `EventSource`（仅支持 GET）。
> 需要使用 `fetch` API + `ReadableStream` 手动解析 SSE 格式，或使用第三方库如 `@microsoft/fetch-event-source`。

```typescript
class SSEClient {
  private abortController: AbortController | null = null
  private reconnectDelay = 1000
  private maxReconnectDelay = 30000

  // 发起聊天请求并建立 SSE 连接
  async chat(message: string, conversationId: string): Promise<void> {
    this.abortController = new AbortController()
    const response = await fetch('/api/seecrab/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, conversation_id: conversationId }),
      signal: this.abortController.signal,
    })
    // 使用 ReadableStream 读取 SSE 格式响应
    // 解析每行 "data: {...}\n\n" → dispatch 到 chatStore
  }

  // 自动重连（指数退避）
  private reconnect(): void {
    setTimeout(() => {
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay)
      // 重连...
    }, this.reconnectDelay)
  }

  // 断开连接
  disconnect(): void { ... }
}
```

### 7.3 Chat Store (stores/chat.ts)

```typescript
export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const currentReply = ref<ReplyState | null>(null)

  // SSE 事件处理
  function handleThinking(event: ThinkingEvent) { ... }
  function handlePlanChecklist(event: PlanChecklistEvent) { ... }
  function handleStepCard(event: StepCardEvent) {
    // 按 step_id 做 upsert
    const existing = currentReply.value?.stepCards.find(c => c.stepId === event.step_id)
    if (existing) {
      Object.assign(existing, mapStepCard(event))  // 更新
    } else {
      currentReply.value?.stepCards.push(mapStepCard(event))  // 新增
    }
  }
  function handleAiText(event: AiTextEvent) { ... }
  function handleTimerUpdate(event: TimerUpdateEvent) { ... }
  function handleDone() { ... }
})
```

### 7.4 核心组件

#### BotReply.vue — 三/四段式容器

```
<template>
  <div class="bot-reply">
    <ReplyHeader :reply="reply" />
    <ThinkingBlock :content="reply.thinking" :done="reply.thinkingDone" />
    <PlanChecklist v-if="reply.planChecklist" :steps="reply.planChecklist" />
    <StepCardList :cards="reply.stepCards" />
    <SummaryOutput v-if="reply.summaryText" :content="reply.summaryText" />
    <AskUserBlock v-if="reply.askUser" :askUser="reply.askUser" />
  </div>
</template>
```

前端根据是否收到 `planChecklist` 自动切换三段/四段式，无需模式判断。

#### StepCard.vue — 条状步骤卡片

```
┌──────────────────────────────────────────────────┐
│  [状态图标]    步骤标题文字               [→]     │
│   (左侧)       (居中)                   (右侧)   │
└──────────────────────────────────────────────────┘
```

- 宽度跟随中间聊天区域（max-w: 720px），高度 40~48px
- 状态图标：running=蓝色 spinner, completed=绿色 ✓, failed=红色 ✗
- 点击 `→` 展开右侧面板；点击卡片其他区域无响应

#### useTimer.ts — 计时器 composable

```typescript
export function useTimer() {
  const ttft = ref<{ state: string; value: number | null }>({ state: 'idle', value: null })
  const total = ref<{ state: string; value: number | null }>({ state: 'idle', value: null })
  const displayTtft = ref(0)
  const displayTotal = ref(0)

  let rafId: number | null = null
  let localStartTime: number | null = null

  function startPhase(phase: 'ttft' | 'total') {
    localStartTime = performance.now()
    rafId = requestAnimationFrame(tick)
  }

  function tick() {
    if (localStartTime) {
      const elapsed = (performance.now() - localStartTime) / 1000
      // 更新对应的 display ref
      rafId = requestAnimationFrame(tick)
    }
  }

  function endPhase(phase: 'ttft' | 'total', value: number) {
    // 停止 rAF，用后端精确值替换
    if (rafId) cancelAnimationFrame(rafId)
    // 更新 ref
  }

  onUnmounted(() => {
    if (rafId) cancelAnimationFrame(rafId)
  })

  return { ttft, total, displayTtft, displayTotal, startPhase, endPhase }
}
```

---

## 8. 错误处理与边界场景

### 8.1 SSE 连接断开

| 端 | 处理 |
|----|------|
| 前端 | `EventSource.onerror` → 指数退避重连(1s/2s/4s/8s，最大 30s)；重连后通过 `GET /api/seecrab/sessions/{id}` 恢复最后状态；显示"连接断开，正在重连..."提示条 |
| 后端 | 断连检测(2s 轮询 `request.is_disconnected()`) → `cancel agent task` → 清理 busy lock |

### 8.2 LLM 标题生成失败

| 场景 | 处理 |
|------|------|
| 超时 (30s) | 降级到 meta 信息标题 |
| 返回空/无效 | 降级到 `"调用 {name} 服务"` |
| Brain 未初始化 | 跳过 LLM，直接用降级标题 |
| 并发超限 | `asyncio.Semaphore(3)` 排队 |

### 8.3 工具执行失败

| 场景 | 处理 |
|------|------|
| `tool_call_end(is_error=true)` | step_card 状态 → `"failed"` |
| Skill 内部失败 | Skill 卡片状态 → `"failed"`，记录失败的子调用 |
| Plan Step 失败 | step_card → `"failed"`，plan_checklist 对应项标记失败 |
| 前端显示 | 红色叉号 + 错误信息摘要 |

### 8.4 并发与竞态

| 场景 | 处理 |
|------|------|
| busy-lock | 复用现有机制，同一会话同时只允许一个 chat 请求 |
| 并行工具调用 | StepAggregator 按 tool_call_start 顺序处理 |
| LLM 标题 vs 事件流 | 标题异步生成，先下发占位标题，生成完成后更新 |
| 多 Agent 并发 | MultiAgentAdapter 用 asyncio.Queue 合并 |

### 8.5 会话恢复

| 场景 | 处理 |
|------|------|
| 页面刷新 | 从 SessionManager 加载历史消息 + 已完成的 step_cards |
| step_card 持久化 | session.add_message 时将 step_cards 作为 metadata 存入 |
| 进行中的回复 | 刷新后显示为 "已中断"，不恢复流式状态 |

### 8.6 性能边界

| 场景 | 处理 |
|------|------|
| 大量 step_cards (>50) | 前端虚拟滚动 |
| step_card.output > 16KB | 截断显示，右侧面板提供完整版 |
| 长对话 | 消息列表虚拟滚动 |
| heartbeat | 30s 无事件 → 后端发送 heartbeat 保活 |

---

## 9. 前端数据类型定义

```typescript
// ═══ SSE 事件类型 ═══

type SSEEventType =
  | 'thinking' | 'plan_checklist' | 'step_card' | 'ai_text'
  | 'ask_user' | 'agent_header' | 'artifact'
  | 'timer_update' | 'heartbeat' | 'done' | 'error'

// ═══ 消息模型 ═══

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  reply?: ReplyState
}

interface ReplyState {
  replyId: string
  agentId: string
  agentName: string
  thinking: string
  thinkingDone: boolean
  planChecklist: PlanStep[] | null
  stepCards: StepCard[]
  summaryText: string
  timer: TimerState
  askUser: AskUserState | null
  artifacts: Artifact[]
  isDone: boolean
}

// ═══ 步骤卡片 ═══

interface StepCard {
  stepId: string
  title: string
  status: 'running' | 'completed' | 'failed'
  sourceType: 'tool' | 'skill' | 'mcp' | 'plan_step'
  cardType: 'search' | 'code' | 'file' | 'analysis' | 'browser' | 'default'
  duration: number | null
  planStepIndex: number | null
  agentId: string
  input: Record<string, unknown> | null
  output: string | null
  absorbedCalls: AbsorbedCall[]
}

interface AbsorbedCall {
  tool: string
  args: Record<string, unknown>
  duration: number | null
  result?: string
}

// ═══ 计时器 ═══

interface TimerState {
  ttft: { state: 'idle' | 'running' | 'done'; value: number | null }
  total: { state: 'idle' | 'running' | 'done'; value: number | null }
}

// ═══ 计划清单 ═══

interface PlanStep {
  index: number
  title: string
  status: 'pending' | 'running' | 'completed' | 'failed'
}

// ═══ 会话 ═══

interface Session {
  id: string
  title: string
  lastMessage: string
  updatedAt: number
  messageCount: number
}

// ═══ Ask User ═══

interface AskUserState {
  question: string
  options: { label: string; value: string }[]
  answered: boolean
  answer?: string
}

// ═══ Artifact ═══

interface Artifact {
  type: string
  fileUrl: string
  filename: string
  mimeType: string
}
```

---

## 10. 后端 Python 数据类定义

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

# ═══ 聚合状态 ═══

class AggregatorState(Enum):
    IDLE = "idle"
    SKILL_ABSORB = "skill_absorb"
    MCP_ABSORB = "mcp_absorb"
    PLAN_ABSORB = "plan_absorb"

class FilterResult(Enum):
    SKILL_TRIGGER = "skill_trigger"
    MCP_TRIGGER = "mcp_trigger"
    WHITELIST = "whitelist"
    USER_MENTION = "user_mention"
    HIDDEN = "hidden"

# ═══ 工作缓冲区 ═══

@dataclass
class PendingCard:
    """聚合状态机的工作缓冲区"""
    step_id: str
    title: str
    title_task: asyncio.Task | None
    status: str = "running"
    source_type: str = ""           # skill | mcp | plan_step
    card_type: str = "default"
    plan_step_index: int | None = None
    agent_id: str = "main"
    t_start: float = 0.0
    input_summary: dict | None = None
    absorbed_calls: list[dict] = field(default_factory=list)
    mcp_server: str | None = None

# ═══ 计时器 ═══

@dataclass
class ReplyTimer:
    reply_id: str
    t_request: float
    t_first_token: float | None = None
    t_done: float | None = None
    step_timers: dict[str, StepTimer] = field(default_factory=dict)

@dataclass
class StepTimer:
    step_id: str
    t_start: float
    t_end: float | None = None

# ═══ 配置 ═══

@dataclass
class StepFilterConfig:
    whitelist: list[str] = field(default_factory=lambda: [
        "web_search", "news_search", "browser_task",
        "generate_image", "deliver_artifacts",
        "delegate_to_agent", "delegate_parallel",
    ])
    skill_triggers: list[str] = field(default_factory=lambda: [
        "load_skill", "run_skill_script",
    ])
    mcp_trigger: str = "call_mcp_tool"
    user_mention_keywords: dict[str, list[str]] = field(default_factory=lambda: {
        "read_file": ["读取", "读", "查看文件", "打开文件"],
        "write_file": ["写入", "写", "创建文件", "生成文件"],
        "run_shell": ["运行", "执行", "跑"],
    })
```

---

## 11. 页面布局与交互

详见需求文档 `20260341547_simple_agent_ui_spec.md`，以下为技术实现要点补充：

### 11.1 两栏 ↔ 三栏切换

- 使用 CSS transition（width + opacity，300ms ease）
- 右侧面板宽度 400px，使用 `v-if` + `Transition` 组件
- 中间聊天区域 `flex:1`，max-width:720px 居中

### 11.2 Welcome 页面

- 通过 `v-if="messages.length === 0"` 条件渲染
- 快捷按钮点击 → `emit('prefill', text)` → ChatInput 预填 + focus

### 11.3 右侧面板

- 点击 StepCard 的 `→` → `uiStore.selectStep(stepId)` → RightPanel 展开
- 面板内容从对应 step_card 事件中提取 `input` / `output`
- 步骤执行中时（status:"running"），面板持续监听 SSE 更新

---

## 12. 与现有代码的集成点

| 集成点 | 现有模块 | 集成方式 |
|--------|---------|---------|
| Agent 执行 | `core/agent.py` | 调用 `chat_with_session_stream()` |
| LLM 标题 | `core/brain.py` | 调用 `think_lightweight()` |
| Session | `sessions/session.py` | 复用 SessionManager |
| 认证 | `api/routes/auth.py` | 复用 JWT 认证中间件 |
| 静态文件 | `api/server.py` | 挂载 SeeCrab dist 到 `/seecrab/` |
| 双循环 | `core/engine_bridge.py` | 复用 `engine_stream()` 桥接 |
| 配置 | `config.py` | 复用 Settings |
| 工具系统 | `tools/catalog.py` | 读取工具 meta（白名单校验） |
| Skill | `skills/` | 读取 Skill meta（标题生成输入） |
| MCP | `tools/handlers/mcp.py` | 读取 MCP Server/Tool meta |

---

## 13. 测试策略

| 层级 | 范围 | 方法 |
|------|------|------|
| L1 单元测试 | StepFilter, StepAggregator, TimerTracker, TitleGenerator, CardBuilder | 纯函数/状态机测试 |
| L2 组件测试 | SeeCrabAdapter (集成子模块) | Mock reason_stream → 验证输出事件序列 |
| L3 集成测试 | SSE 路由端到端 | Mock Agent → 验证 HTTP SSE 响应 |
| L4 前端测试 | Vue 组件 | Vitest + Vue Test Utils |

重点测试用例：
- 聚合状态机的所有状态转移路径
- Skill/MCP 嵌套场景
- LLM 标题生成超时降级
- 计时器精度校验
- 并行工具调用的卡片独立性
- Plan 模式全吸收

---

## 附录 A：与现有 adapter 的关系

现有文件 `tool_filter.py` 和 `card_type_mapper.py` 保留不变（旧代码兼容），SeeCrab 使用新的 `step_filter.py` 和 `card_builder.py`。

未来可考虑废弃旧文件，但不在本次设计范围内。

## 附录 B：已知问题（来自 memory）

- **P0: MCP 卡片体验缺失** → 本设计已解决（MCP 使用 pending card + LLM 标题 + 吸收连续调用）
- **P1: 聚合死代码** → 本设计使用全新的 StepAggregator，旧代码不再使用
