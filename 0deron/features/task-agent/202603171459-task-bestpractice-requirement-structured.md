# 最佳实践任务管理系统 — 结构化需求文档

> 原始需求: `202603171459-task-bestpractice-requirement.md`
> 交互模拟: `mockup-bestpractice-interaction.html`
> 整理日期: 2026-03-17

---

## 1. 术语表

> 本节统一全文术语，标明**已有代码实体**与**待新增实体**，避免产品 / 前端 / 后端沟通歧义。

### 1.1 核心业务概念

| 产品术语 | 英文 | 已有 / 新增 | 代码实体 | 说明 |
|---------|------|-----------|---------|------|
| 最佳实践 | Best Practice | **新增** | `BestPractice` | 业务沉淀的任务模板，编排多个子任务顺序执行 |
| 子任务 | SubTask | 复用已有 | `AgentProfile`（`agents/profile.py`） | 每个子任务对应一个 Agent 角色，能力配置全部在 AgentProfile 中管理 |
| 委派 | Delegation | 已有 | `DelegationRequest`（`agents/orchestrator.py`） | 主 Agent 向子 Agent 分发任务的请求 |
| 编排器 | Orchestrator | 已有 | `AgentOrchestrator`（`agents/orchestrator.py`） | 负责 Agent 间调度、委派、超时管理 |
| 技能 | Skill | 已有 | `SkillManager`（`core/skill_manager.py`）+ `skills/` 目录 | SKILL.md 定义的技能包，已支持 per-agent 过滤 |
| 工具 | Tool | 已有 | `tools/catalog.py` + `handlers/` | 单个可执行的工具函数，**当前全局共享，需升级为 per-agent** |
| MCP 服务 | MCP Server | 已有 | `mcp_client` + `mcp_catalog`（全局单例） | **当前全局共享，需升级为 per-agent** |
| 定时任务 | Scheduled Task | 已有 | `ScheduledTask`（`scheduler/task.py`） | 支持 ONCE / INTERVAL / CRON 三种触发 |
| Agent 实例池 | Instance Pool | 已有 | `AgentInstancePool`（`agents/factory.py`） | 按 `{session_id}::{profile_id}` 缓存 Agent 实例 |
| 任务队列 | Task Queue | 已有 | `TaskQueue` / `QueuedTask`（`agents/task_queue.py`） | 优先级队列，支持 URGENT → BACKGROUND 5 级 |
| BP 状态管理器 | BPStateManager | **新增** | `BPStateManager`（`bestpractice/state_manager.py`） | BP 实例生命周期管理；`create_instance(bp_config, session_id) -> BPInstanceSnapshot`（返回完整快照） |

> **AgentProfile 升级说明**：当前 `AgentProfile` 仅支持 `skills` + `skills_mode`（INCLUSIVE/EXCLUSIVE/ALL）进行 per-agent 技能过滤。本需求要求**新增对称的字段**，使 tools 和 MCPs 也支持 per-agent 配置：
>
> | 现有字段 | 新增字段 | 模式 | 默认值 |
> |---------|---------|------|-------|
> | `skills` + `skills_mode` | — | INCLUSIVE / EXCLUSIVE / ALL | ALL（继承主 Agent 全部技能） |
> | — | `tools` + `tools_mode` | INCLUSIVE / EXCLUSIVE / ALL | ALL（继承主 Agent 全部工具） |
> | — | `mcps` + `mcps_mode` | INCLUSIVE / EXCLUSIVE / ALL | ALL（继承主 Agent 全部 MCP） |
>
> 对应 `AgentFactory` 需新增 `_apply_tool_filter()` 和 `_apply_mcp_filter()`，逻辑与已有 `_apply_skill_filter()` 对称。
> **不配置（ALL）时与主 Agent 完全一致**，无需额外处理。

### 1.2 触发与状态

| 产品术语 | 英文 | 已有 / 新增 | 代码实体 | 说明 |
|---------|------|-----------|---------|------|
| 触发类型 | Trigger Type | **新增独立机制** | `BPEngine` 系统提示引导 | BP 触发不扩展 ScheduledTask 的 `TriggerType` 枚举。`COMMAND`/`CONTEXT` 通过 BP_STATIC 系统提示让 LLM 直接识别；`CRON` 复用已有 `ScheduledTask` + CRON，在 prompt 中注入 `bp_start` 调用指令；`EVENT` 通过 `pending_user_inserts` 注入触发消息；`UI_CLICK` 通过前端 API 直接构造 `bp_start` 参数调用后端 |
| 任务状态 | Task Status | 已有 | `TaskStatus`（`core/agent_state.py`） | `IDLE` / `REASONING` / `ACTING` / `COMPLETED` / `FAILED` 等 |
| 运行模式 | Run Mode | **新增** | `runMode: 'manual' \| 'auto'` | 手动模式（默认，逐步确认）/ 自动模式（连续执行） |
| 子任务进度 | SubTask Progress | **新增** | `subtaskStatus: 'pending' \| 'current' \| 'done' \| 'stale' \| 'failed'` | 总任务卡片中每个子任务的进度标记（H7 改进：增加 failed 状态） |
| 推断冷却 | Infer Cooldown | **新增** | `inferCooldown: number` | 选择自由模式后 5 轮内不再推断触发 |

### 1.3 前端 UI 组件

| 产品术语 | 英文 | 已有 / 新增 | 代码实体 | 说明 |
|---------|------|-----------|---------|------|
| 思考气泡 | Thinking Block | 已有 | `ThinkingBlock.vue` / `ReplyState.thinking` | `progress_activity` 旋转 → `psychology` + "思考完成" |
| 步骤卡片 | Step Card | 已有 | `StepCard.vue` / `StepCard` 接口 | 工具/技能/MCP 调用的可视化卡片 |
| 委派卡片 | Delegation Card | 已有样式 | `StepCard { sourceType: 'skill' }` | 主级别 StepCard，`smart_toy` 图标 |
| 子步骤卡片 | Sub-step Card | 已有样式 | `StepCard { agentId ≠ 'main' }` + `.is-sub-agent` | 缩进 24px，accent 色背景 |
| 用户交互提示 | Ask User Block | 已有 | `AskUserBlock.vue` / `AskUserState` | 带选项按钮的问答块 |
| 计划清单 | Plan Checklist | 已有 | `PlanChecklist.vue` / `PlanStep[]` | 编号步骤列表 |
| 步骤详情面板 | Step Detail | 已有 | `StepDetail.vue` / `InputViewer` + `OutputViewer` + `AbsorbedCalls` | 右侧面板：输入 JSON / 输出内容 / 子调用 |
| 总任务卡片 | Task Progress Card | **新增** | `TaskProgressCard.vue` / `BestPracticeState` | 进度条 + 手动/自动切换，所有实例状态同步 |
| 结果看板 | SubTask Output Panel | **新增** | `SubtaskOutputPanel.vue` | 右侧面板的子任务输出编辑视图 |
| 完成块 | SubTask Complete Block | **新增** | `SubtaskCompleteBlock.vue` | 摘要 + [查看结果] + [进入下一步] |

### 1.4 SSE 事件

| 事件 | SSE `type` | 已有 / 新增 | 后端来源 → 前端处理 |
|------|-----------|-----------|-------------------|
| 思考内容 | `thinking` | 已有 | `SeeCrabAdapter._handle_thinking()` → `ReplyState.thinking` |
| 步骤卡片 | `step_card` | 已有 | `StepAggregator` → `CardBuilder` → `chatStore._upsertStepCard()` |
| 回复文本 | `ai_text` | 已有 | `_handle_text_delta()` → `ReplyState.summaryText` |
| 用户交互 | `ask_user` | 已有 | `_map_ask_user()` → 渲染 `AskUserBlock` |
| Agent 切换 | `agent_header` | 已有 | `_handle_agent_switch()` → 新 `ReplyState` |
| 计划清单 | `plan_checklist` | 已有 | `on_plan_created()` → `ReplyState.planChecklist` |
| 计时更新 | `timer_update` | 已有 | `TimerTracker.make_event()` → TTFT / 总耗时 |
| 完成 | `done` | 已有 | 流结束 → `ReplyState.isDone = true` |
| **最佳实践进度** | `bp_progress` | **新增** | 最佳实践引擎 → 更新所有 `TaskProgressCard` |
| **子任务输出** | `bp_subtask_output` | **新增** | 子任务完成 → 结果看板数据 |

### 1.5 StepCard 字段枚举值

| 字段 | 可选值 | 说明 |
|------|-------|------|
| `status` | `'running'` / `'completed'` / `'failed'` | 步骤执行状态 |
| `sourceType` | `'tool'` / `'skill'` / `'mcp'` / `'plan_step'` | 调用来源类型 |
| `cardType` | `'search'` / `'code'` / `'file'` / `'analysis'` / `'browser'` / `'default'` | 卡片视觉类型（决定图标） |

### 1.6 Pinia Store

| Store | 文件 | 职责 | 关键方法 |
|-------|-----|------|---------|
| `useChatStore` | `stores/chat.ts` | 消息列表 + 事件分发 | `dispatchEvent()`, `_upsertStepCard()`, `addUserMessage()` |
| `useUIStore` | `stores/ui.ts` | 面板开关 + 选中状态 | `selectStep()`, `closeRightPanel()` |
| `useSessionStore` | `stores/session.ts` | 会话管理 | `createSession()`, `selectSession()` |
| **`useBestPracticeStore`** | **待新增** | **最佳实践运行态** | `startBP()`, `nextSubtask()`, `toggleRunMode()`, `updateSubtaskOutput()` |

### 1.7 事件流转链路

```
后端 Agent 引擎 (AgentOrchestrator → DelegationRequest → AgentProfile)
  ↓ 原始事件 (thinking_delta, tool_call_start/end, ...)
SeeCrabAdapter._process_event()
  ↓ 标准化事件 (thinking, step_card, ai_text, ask_user, bp_progress, ...)
SSE Stream (data: {JSON}\n\n)
  ↓
前端 SSEClient.sendMessage() 读取流
  ↓
useChatStore().dispatchEvent(event) + useBestPracticeStore()
  ↓ 状态突变 (currentReply, messages[], bestPracticeState)
Vue 响应式 → 组件自动更新（含所有 TaskProgressCard 实例同步刷新）
```

---

## 2. 系统概述

构建一个**最佳实践任务编排系统**，基于现有 `AgentOrchestrator` 的多 Agent 委派能力，新增「最佳实践」（Best Practice）层 —— 业务沉淀的任务模板。每个最佳实践由多个**子任务**（各对应一个 `AgentProfile`）按顺序执行，子任务间通过 JSON 数据串联。

### 2.1 概念模型

```
┌─────────────────────────────────────────────────────────┐
│  BestPractice（最佳实践 / 任务模板）                       │
│                                                          │
│  ┌───────────┐     ┌───────────┐     ┌───────────┐     │
│  │ SubTask 1  │────▶│ SubTask 2  │────▶│ SubTask 3  │    │
│  │AgentProfile│     │AgentProfile│     │AgentProfile│    │
│  └───────────┘     └───────────┘     └───────────┘     │
│       │                 │                  │             │
│  Skill + Tool      Skill + Tool       Skill + Tool      │
│       │                 │                  │             │
│  输入JSON ──▶ 输出JSON ──▶ 输出JSON ──▶ 最终输出          │
│                                                          │
│  ↑ AgentOrchestrator 调度    ↑ DelegationRequest 串联    │
└─────────────────────────────────────────────────────────┘
```

### 2.2 关键特性

| 特性 | 说明 |
|------|------|
| 子任务独立性 | 每个子任务引用自己的 `AgentProfile`，技能/工具/MCP 配置由 Profile 管理（不在 BestPractice 中重复定义） |
| 数据流转 | 子任务间通过结构化 JSON 串联，当前子任务参考下游 `input_schema` 整理输出 |
| 人机交互 | 支持用户审查、编辑中间结果（`ask_user` + 结果看板） |
| 双运行模式 | 手动模式（逐步确认）+ 自动模式（连续执行） |

---

## 3. 触发机制

最佳实践支持 **5 种触发方式**。触发机制**不扩展** `ScheduledTask` 的 `TriggerType` 枚举，而是通过 BP 独立机制实现（L5 改进：与技术设计对齐）：

| # | 触发方式 | 实现机制 | 示例 | 说明 |
|---|---------|---------|------|------|
| 1 | **系统指令** | LLM 识别 `pattern` → 调用 `bp_start` | "执行市场调研" | 用户明确指令触发 |
| 2 | **事件驱动** | 外部事件注入消息 → MasterAgent 调用 `bp_start` | "当新数据到达时执行" | 外部事件回调触发 |
| 3 | **上下文推断** | LLM 识别 `conditions` → `ask_user` 选择 | 聊天中自动识别触发条件 | 根据对话上下文智能匹配 |
| 4 | **定时调度** | 复用 `ScheduledTask` + CRON → prompt 注入 `bp_start` 指令 | "每周一凌晨1点" | 复用已有调度器 |
| 5 | **页面点击** | 前端 API `POST /api/bp/start` → 注入消息 | 用户在 UI 上选择 | 手动选取触发（H8 改进） |

### 3.1 上下文推断触发的特殊交互

当系统通过用户上下文判定可能触发某个最佳实践时，需要给用户**选择权**：

```
┌──────────────────────────────────────────────────┐
│  系统检测到您的对话匹配最佳实践「XXX」               │
│                                                   │
│  请选择：                                          │
│  ┌──────────┐    ┌──────────────┐                 │
│  │ 自由模式  │    │ 最佳实践模式  │                 │
│  └──────────┘    └──────────────┘                 │
│                                                   │
│  自由模式: 按普通对话继续（5轮内不再触发推断）       │
│  最佳实践模式: 进入任务流程执行                      │
└──────────────────────────────────────────────────┘
```

**按钮交互规则**：
- 用户点击按钮后，按钮文字作为**用户消息**发送到聊天区（复用 `AskUserBlock` 组件）
- 按钮点击后禁用，防止重复触发
- 选择「自由模式」后，5 轮用户输入内不再进行推断触发（`inferCooldown` 计数器）

---

## 4. 子任务数据流

### 4.1 Schema 设计原则

每个子任务在 `BestPractice` 配置中只定义 `input_schema`（我需要什么），**不定义 `output_schema`**。输出格式由系统自动推导：

```
子任务 N 的输出要求 = 子任务 N+1 的 input_schema
最后一个子任务的输出要求 = BestPractice.final_output_schema（可选）
```

- **`final_output_schema` 可选**：如果配置了，LLM 参考该 Schema 整理最终输出；如果未配置，LLM 自行决定输出格式
- 第一个子任务的 `input_schema` 即为整个最佳实践的入参要求

### 4.2 输入来源

| 来源 | 场景 | 说明 |
|------|------|------|
| 上游子任务输出 | 串联执行时 | 上一个子任务按下游 `input_schema` 整理的 JSON |
| 用户上下文提取 | `CONTEXT` 触发时 | 从对话上下文中按第一个子任务的 `input_schema` 提取 |

### 4.3 输出规范

每个子任务完成后，**必须执行两步输出**：

```
子任务（AgentProfile）执行完所有步骤
        │
        ▼
  ① 简要说明运行结果（文本摘要 → ai_text 事件）
        │
        ▼
  ② 参考下游子任务的 input_schema（或 final_output_schema）
     → 整理生成 JSON（→ bp_subtask_output 事件）
        │
        ▼
    子任务完成
```

### 4.4 输入不足时的补充

如果用户提供的信息不满足第一个子任务的 `input_schema` 要求，由 MasterAgent 通过 `ask_user` 事件提示用户补充必要信息。

**子任务衔接时的输入校验**：每个子任务执行前，系统校验上游输出是否满足当前子任务 `input_schema` 的 `required` 字段。如果存在缺失字段：

| 场景 | 行为 |
|------|------|
| **手动模式** | 在 `[进入下一步]` 交互中，MasterAgent 通过 `ask_user` 列出缺失字段，引导用户补充 |
| **自动模式** | 自动执行暂停，MasterAgent 通过 `ask_user` 列出缺失字段，引导用户补充。用户补充后自动恢复执行 |

> 此校验发生在 BPEngine 层面（非 SubAgent 内部），确保子任务在信息充分时才启动委派。

---

## 5. 运行模式

运行模式切换控件位于**总任务卡片（`TaskProgressCard`）的右上角**。

### 5.1 手动模式（默认）

```
SubTask 1 执行 → 等待用户操作 → [查看结果] / [进入下一步] → SubTask 2 执行 → ...
```

- 每个子任务完成后**暂停**
- 用户可以查看和编辑中间输出（结果看板）
- 用户点击「进入下一步」继续（按钮文字作为用户消息发送）

### 5.2 自动模式

```
SubTask 1 执行 → 自动 → SubTask 2 执行 → 自动 → SubTask 3 执行 → ... → 完成
```

- 子任务完成后**自动进入**下一步
- 无需等待用户点击
- **自动暂停条件**：如果下一个子任务的 `input_schema.required` 字段在上游输出中缺失，自动模式暂停，通过 `ask_user` 请求用户补充缺失信息。用户补充后自动恢复执行

---

## 6. 交互设计

### 6.1 总任务卡片（TaskProgressCard）

进入最佳实践模式后，**每个子任务执行前**都会渲染一个总任务卡片实例，确保用户始终可见全局进度：

```
┌─────────────────────────────────────────────────┐
│  📋 商业模式调研报告              [手动 ○ / 自动] │
│                                                  │
│  ●──────○──────○                                │
│  1.市场调研  2.数据分析  3.报告生成               │
│  (当前)      (待执行)    (待执行)                 │
└─────────────────────────────────────────────────┘
```

**交互行为**：
- 卡片整体**可点击**，点击后打开右侧结果看板，定位到**当前正在执行的子任务**
- 进度条实时更新：`pending` → `current`（脉冲动画）→ `done`（绿色）
- 右上角手动/自动模式切换按钮（点击不触发卡片跳转，`event.stopPropagation()`）

**状态同步规则**：
- 聊天区内会存在**多个** `TaskProgressCard` 实例（每个子任务前各渲染一个）
- 当子任务完成或进度推进时，**所有已渲染的卡片实例**必须同步更新进度状态
- 当用户在任意一个卡片上切换运行模式（手动 ↔ 自动）时，**所有卡片实例**的模式状态同步切换
- 实现方式：所有卡片绑定同一个 `BestPracticeState`（`useBestPracticeStore`），通过响应式自动同步

### 6.2 主聊天区 — 子任务执行流程

每个子任务的展示**复用现有 SubAgent 组件**（`ThinkingBlock` + `StepCard`），不使用 `AgentSummaryBlock`：

```
┌─────────────────────────────────────────────────┐
│  [TaskProgressCard - 显示当前进度]                │
│                                                  │
│  ┌─ 思考完成 ─────────────────── ▸ ┐            │
│  │ (可展开查看推理过程)              │            │
│  └──────────────────────────────────┘            │
│                                                  │
│  ✅ 🤖 委派 research-agent: 市场调研...  285.3s ▸ │  ← 主level StepCard (DelegationRequest)
│    ✅ 🔍 搜索 "Token 商业模式 2025"      3.8s ▸  │  ← 子步骤 (.is-sub-agent)
│    ✅ 🌐 浏览 Token Factory 白皮书       12.4s ▸  │
│    ✅ 🔍 搜索 "Token Factory 盈利模式"   2.1s ▸  │
│    ✅ 🌐 浏览 CrunchBase 融资信息        8.7s ▸  │
│    ✅ 📊 分析竞品对比数据                5.2s ▸  │
│                                                  │
│  ┌ ✅ 子任务 1「市场调研」       已完成 ──────┐  │
│  │ 摘要：已完成全面调研，收集了...             │  │
│  │                                            │  │
│  │  [查看结果]    [进入下一步]                  │  │
│  └────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**关键展示规则**：
- **ThinkingBlock**：与现有组件一致（`progress_activity` 旋转 → `psychology` 图标 + "思考完成"），可展开查看推理内容
- **委派卡片**：主 agent 级别的 StepCard（不缩进），`smart_toy` 图标，标题为 "委派 xxx-agent: ..."，对应一次 `DelegationRequest`
- **子步骤卡片**：缩进 24px，带 accent 色背景，与现有 `.is-sub-agent` 样式一致
- **不使用 AgentSummaryBlock**：子任务完成后直接显示完成块（`SubtaskCompleteBlock`）

**每个 StepCard 可点击**：
- 点击箭头或卡片整体 → 右侧面板切换为**步骤详情模式**（`StepDetail`）
- 显示：状态徽章、耗时/Token数、输入JSON（`InputViewer`）、输出内容（`OutputViewer`）、子调用列表（`AbsorbedCalls`）
- Running 状态的卡片也可点击（显示已有 input，output 为空）
- 卡片完成后面板内容自动刷新

### 6.3 按钮交互规则

| 按钮 | 点击行为 | 是否生成用户消息 |
|------|---------|----------------|
| 自由模式 | 发送用户消息 "自由模式" → 进入普通对话 | ✅ 是 |
| 最佳实践模式 | 发送用户消息 "最佳实践模式" → 开始执行 | ✅ 是 |
| 查看结果 | 直接打开右侧结果看板（`SubtaskOutputPanel`） | ❌ 否（纯UI操作） |
| 进入下一步 | 发送用户消息 "进入下一步" → 执行下一个子任务 | ✅ 是 |
| 总任务卡片 | 打开右侧结果看板，定位到当前子任务 | ❌ 否（纯UI操作） |
| 手动/自动切换 | 切换 `BestPracticeState.runMode`，同步所有卡片实例 | ❌ 否（纯UI操作） |

### 6.4 右侧面板 — 双模式

右侧面板有两种模式，由不同的点击入口触发：

#### 模式 A：步骤详情（StepDetail — 已有组件）

**触发方式**：点击聊天区中任意 StepCard

```
┌─────────────────────────────────────────┐
│  🔍 搜索 "Token 商业模式 2025"    [✕]   │
│─────────────────────────────────────────│
│                                         │
│  ┌ ✅ 完成 ┐                            │
│  └────────┘                             │
│                                         │
│  ⏱ 耗时 3.8s  📥 输入 ~42  📤 输出 ~156 │
│─────────────────────────────────────────│
│  ▎输入 (InputViewer)                    │
│  ┌──────────────────────────────────┐   │
│  │ {                                │   │
│  │   "query": "Token 超级工厂...",  │   │
│  │   "engine": "google"             │   │
│  │ }                                │   │
│  └──────────────────────────────────┘   │
│                                         │
│  ▎输出 (OutputViewer)                   │
│  ┌──────────────────────────────────┐   │
│  │ 找到 12 条相关结果，包括...       │   │
│  └──────────────────────────────────┘   │
│                                         │
│  ▎子调用 (AbsorbedCalls) (3)           │
│  ┌ 💻 web_search          2.1s ┐       │
│  ┌ 💻 result_parser       1.2s ┐       │
│  ┌ 💻 relevance_filter    0.5s ┐       │
└─────────────────────────────────────────┘
```

#### 模式 B：子任务结果看板（SubtaskOutputPanel — 新增组件）

**触发方式**：点击「查看结果」按钮 或 点击 `TaskProgressCard`

```
┌─────────────────────────────────────────┐
│  📋 商业模式调研报告               [✕]  │
│─────────────────────────────────────────│
│                                         │
│  子任务进度                              │
│  [● 市场调研] [● 数据分析] [○ 报告生成]  │
│     ↑ 当前选中                          │
│─────────────────────────────────────────│
│                                         │
│  ▎输出数据（可编辑）                     │
│  ┌──────────────────────────────────┐   │
│  │ topic    [Token 超级工厂商业模式]│   │
│  │ summary  [自动化 Token 生成...]  │   │
│  │ findings                         │   │
│  │   [技术架构：TokenML引擎...]  ✕  │   │
│  │   [盈利模式：SaaS+抽成...]    ✕  │   │
│  │   [竞争壁垒：3项专利...]      ✕  │   │
│  │               [+ 新增]           │   │
│  └──────────────────────────────────┘   │
│                                         │
│                          [确认更新]      │
└─────────────────────────────────────────┘
```

**导航规则**：
- 点击 `TaskProgressCard` → 打开看板，**定位到当前正在执行的子任务**
- 点击「查看结果」→ 打开看板，定位到**对应的已完成子任务**
- 已完成的子任务 pill（绿色）可点击切换查看历史输出
- 未完成的子任务 pill（灰色）可点击，但显示空状态提示：「子任务尚未执行完成，完成后将在此处展示可编辑的输出数据」
- 子任务运行完成后，如果看板正在显示该子任务，自动刷新为可编辑表单

**输出编辑说明**：
- JSON 数据按下游 `input_schema` 字段渲染为表单
- 文本字段 → 可直接编辑
- 数组字段 → 提供「新增」「删除」操作
- 「确认更新」按钮保存编辑后的 JSON

### 6.5 交互流程图

```
用户输入
  │
  ▼
系统检测匹配 BestPractice（CONTEXT 触发）
  │
  ▼
展示 AskUserBlock: [自由模式] [最佳实践模式]
  │
  ├─ 用户点击 [自由模式] ──▶ 发送用户消息 → 普通对话（inferCooldown = 5）
  │
  └─ 用户点击 [最佳实践模式] ──▶ 发送用户消息 → 进入任务流程
        │
        ▼
  ┌─────────────────────────────────────────────────┐
  │  渲染 TaskProgressCard（进度: ●○○）              │
  │                                                  │
  │  ┌─ SubTask 1 ──────────────────────────────┐   │
  │  │  ThinkingBlock（思考中... → 思考完成）     │   │
  │  │  DelegationRequest → 委派卡片（主level）  │   │
  │  │  子步骤卡片（.is-sub-agent，逐个出现）     │   │
  │  │       ↓                                   │   │
  │  │  SubtaskCompleteBlock                     │   │
  │  │  + [查看结果] [进入下一步]                 │   │
  │  └───────────────────────────────────────────┘   │
  │       │                                          │
  │  ┌────┴──────┐                                   │
  │  │手动模式    │自动模式                           │
  │  │等待点击    │自动继续                           │
  │  └────┬──────┘                                   │
  │       ▼                                          │
  │  所有 TaskProgressCard 同步刷新（进度: ✓●○）      │
  │  渲染新 TaskProgressCard                          │
  │                                                  │
  │  ┌─ SubTask 2 ──────────────────────────────┐   │
  │  │  （同上流程）                              │   │
  │  └───────────────────────────────────────────┘   │
  │       │                                          │
  │       ▼                                          │
  │  所有 TaskProgressCard 同步刷新（进度: ✓✓●）      │
  │  渲染新 TaskProgressCard                          │
  │                                                  │
  │  ┌─ SubTask 3 ──────────────────────────────┐   │
  │  │  （同上流程）                              │   │
  │  └───────────────────────────────────────────┘   │
  │       │                                          │
  │       ▼                                          │
  │  BestPractice「XXX」全部完成                      │
  │  [查看最终报告]                                   │
  └─────────────────────────────────────────────────┘
```

---

## 7. 配置结构

> **设计原则**：BestPractice 只关注**编排和数据流**，不重复定义 Agent 能力。
> 技能 / 工具 / MCP 配置全部由各子任务引用的 `AgentProfile` 管理。

```yaml
best_practice:
  id: "market-research-report"
  name: "市场调研报告"
  description: "从市场调研到报告生成的完整流程"

  # 可选：最终输出格式。配置后 LLM 参考此 Schema 整理最终交付物；
  # 不配置则由 LLM 自行决定输出格式。
  final_output_schema:
    type: object
    properties:
      title: { type: string }
      report_path: { type: string }
      page_count: { type: integer }

  triggers:
    - type: command        # → TriggerType.COMMAND
      pattern: "执行市场调研"
    - type: event          # → TriggerType.EVENT
      event: "new_market_data"
    - type: context        # → TriggerType.CONTEXT
      conditions: ["市场", "调研", "竞品"]
    - type: schedule       # → TriggerType.CRON (已有)
      cron: "0 1 * * 1"

  subtasks:
    - id: "research"
      name: "市场调研"
      agent_profile: "research-agent"  # → AgentProfile.id（技能/工具/MCP 在 Profile 中配置）
      input_schema:                    # 本子任务需要的输入（也是整个 BP 的入参）
        type: object
        properties:
          topic: { type: string, required: true }
          scope: { type: string }

    - id: "analysis"
      name: "数据分析"
      agent_profile: "analysis-agent"
      input_schema:                    # = 上游 research 子任务的输出要求
        type: object
        properties:
          findings: { type: array, required: true }
          summary: { type: string }

    - id: "report"
      name: "报告生成"
      agent_profile: "report-agent"
      input_schema:                    # = 上游 analysis 子任务的输出要求
        type: object
        properties:
          insights: { type: array, required: true }
          charts: { type: array }
      # report 是最后一个子任务，其输出要求 = best_practice.final_output_schema
```

**Schema 推导链路**：
```
用户输入 ──▶ research.input_schema
                    │
              research 执行
                    │ 输出参考 analysis.input_schema
                    ▼
            analysis.input_schema
                    │
              analysis 执行
                    │ 输出参考 report.input_schema
                    ▼
              report.input_schema
                    │
               report 执行
                    │ 输出参考 final_output_schema（可选，无则 LLM 自由输出）
                    ▼
              最终交付物
```

---

## 8. 核心设计要点总结

| 维度 | 要点 |
|------|------|
| **架构复用** | 基于已有 `AgentOrchestrator` + `DelegationRequest` + `AgentProfile` 构建 |
| **AgentProfile 升级** | 新增 `tools` + `tools_mode`、`mcps` + `mcps_mode`，与已有 `skills` + `skills_mode` 对称，默认 ALL（继承主 Agent） |
| **配置职责分离** | BestPractice 只管编排和数据流；技能/工具/MCP 全部由 `AgentProfile` 管理，不重复定义 |
| **数据流** | 子任务间通过 JSON 串联，当前子任务参考下游 `input_schema` 整理输出 |
| **Schema 推导** | 只定义 `input_schema`，不定义 `output_schema`；系统自动推导输出要求 = 下游 `input_schema` |
| **最终输出** | `final_output_schema` 可选：配置后 LLM 参考输出，未配置则 LLM 自行决定格式 |
| **输出规范** | 每个子任务必须：① `ai_text` 文本摘要 ② `bp_subtask_output` 结构化 JSON |
| **输入校验** | 输入不足时 `ask_user` 补充，不静默跳过 |
| **运行模式** | `runMode: 'manual'`（默认）/ `'auto'`，切换控件在 `TaskProgressCard` 右上角 |
| **模式同步** | 切换运行模式时，聊天区内**所有** `TaskProgressCard` 实例同步更新 |
| **进度同步** | 子任务完成时，**所有** `TaskProgressCard` 实例同步刷新进度状态 |
| **触发方式** | BP 独立触发机制：`COMMAND` / `EVENT` / `CONTEXT` / `CRON` / `UI_CLICK`，不扩展 ScheduledTask 的 TriggerType 枚举（L5 改进） |
| **上下文触发** | 复用 `AskUserBlock`，给用户选择权：自由模式 vs 最佳实践模式 |
| **自由模式冷却** | `inferCooldown` 计数器，5 轮内不再推断 |
| **子任务展示** | 复用 `ThinkingBlock` + `StepCard`，不使用 `AgentSummaryBlock` |
| **按钮消息** | 「进入下一步」等发送用户消息；「查看结果」「模式切换」为纯UI操作 |
| **右侧面板** | 双模式：`StepDetail`（点卡片）/ `SubtaskOutputPanel`（点查看结果 / `TaskProgressCard`） |
| **新增 Store** | `useBestPracticeStore` 管理运行态，所有 `TaskProgressCard` 绑定同一 state |
| **任务取消** | 用户可通过 `bp_cancel` 工具取消 BP 实例，状态 → `CANCELLED`（H5 改进） |
| **输出查询** | MasterAgent 可通过 `bp_get_output` 获取完整子任务输出，用于 Chat-to-Edit（H2 改进） |
| **模式切换 API** | 前端通过 `PUT /api/bp/run-mode` REST API 切换手动/自动模式，非工具调用（H4 改进） |
