# SeeCrab WebApp 架构设计方案

> 基于 OpenAkita 现有流式基础设施，为非任务场景 UI 设计规范实现前端 WebApp。
> 日期：2026-03-15

---

## 1. 现有基础设施评估

| 组件 | 状态 | 说明 |
|------|------|------|
| `ReasoningEngine.reason_stream()` | 生产就绪 | 50+ 事件类型，ReAct 推理循环 |
| `/api/chat` SSE 端点 | 生产就绪 | 直接转发引擎原始事件 |
| WebSocket 广播 | 生产就绪 | `broadcast_event()` 多客户端推送 |
| 双事件循环桥接 | 生产就绪 | `engine_bridge.py` 跨线程安全 |
| AgentInstancePool | 生产就绪 | 每会话独立 Agent 实例 |

---

## 2. 核心差距分析

UI 规范定义了 5 种前端消息类型，与现有 SSE 原始事件之间存在语义鸿沟：

| Spec 消息类型 | 现有 SSE 事件 | 差距 |
|---|---|---|
| `thinking` | `thinking_start` + N × `thinking_delta` + `thinking_end` | 需要聚合流式片段 |
| `plan_checklist` | `plan_created` + `plan_step_updated` | 结构近似，需转换字段 |
| `step_card` | `tool_call_start` + `tool_call_end` | **最大差距**：需过滤系统工具 + 聚合同类调用 + 生成语义标题 + 推断 card_type |
| `ai_text` | `text_delta` | 直接映射 |
| `done` | `done` | 需附加 TTFT/Total 计时 |

---

## 3. 总体架构

### 3.1 Protocol Adapter 模式

**核心原则**：引擎层零改动，新增协议适配层完成所有转换。

```
ReasoningEngine.reason_stream()    ← 引擎层：保持不变，输出原始事件
        │
        ▼
   SeeCrabAdapter                  ← 新增：协议适配层
   (过滤 + 聚合 + 语义化 + card_type 推断)
        │
        ▼
   POST /api/seecrab/chat → SSE   ← 新增：SeeCrab 专用端点
        │
        ▼
   SeeCrab Frontend (React)        ← 新增：独立前端 Web 应用
```

### 3.2 架构全景图

```
┌─────────────────────────────────────────────────────────┐
│                     Architecture                        │
│                                                         │
│  ReasoningEngine.reason_stream()                        │
│  (50+ raw events, 零改动)                                │
│         │                                               │
│         ▼                                               │
│  ┌─────────────────────────────┐                        │
│  │  SeeCrabAdapter             │ ← 新增，核心模块        │
│  │  ├─ ToolFilter (配置化)      │   过滤系统工具          │
│  │  ├─ StepAggregator          │   聚合同类步骤          │
│  │  │   └─ LLM 生成语义标题     │   用小模型异步生成      │
│  │  └─ CardTypeMapper          │   tool→card_type 映射   │
│  └─────────────────────────────┘                        │
│         │                                               │
│         ▼                                               │
│  POST /api/seecrab/chat → SSE                           │
│  (5 种 UI 事件: thinking, plan_checklist,                │
│   step_card, ai_text, done)                             │
│         │                                               │
│         ▼                                               │
│  ┌─────────────────────────────┐                        │
│  │  SeeCrab Frontend (React)   │ ← 纯 Web, 笨客户端     │
│  │  ├─ useSSEChat hook         │   SSE 连接管理          │
│  │  ├─ BotReply (3/4 段式)     │   自适应渲染            │
│  │  ├─ StepCard (card_type)    │   按类型渲染图标/样式    │
│  │  └─ RightPanel (样式A)      │   步骤 I/O 详情         │
│  └─────────────────────────────┘                        │
└─────────────────────────────────────────────────────────┘
```

---

## 4. 后端：SeeCrabAdapter 详细设计

### 4.1 模块结构

```
src/openakita/api/adapters/
  __init__.py
  seecrab_adapter.py     ← 核心：引擎事件 → SeeCrab UI 事件
  tool_filter.py         ← 工具过滤规则（展示/隐藏/聚合）
  step_aggregator.py     ← 连续同类工具聚合器 + LLM 标题生成
  card_type_mapper.py    ← tool_name → card_type 映射
```

### 4.2 SeeCrabAdapter 核心逻辑

```python
class SeeCrabAdapter:
    """将 ReasoningEngine 原始事件流转换为 SeeCrab UI 事件流。"""

    def __init__(self, brain=None):
        self.tool_filter = ToolFilter()
        self.aggregator = StepAggregator(brain=brain)
        self.card_mapper = CardTypeMapper()
        self._ttft_ms: float | None = None
        self._start_time: float = 0

    async def adapt(self, raw_events: AsyncIterator[dict]) -> AsyncIterator[dict]:
        self._start_time = time.time()

        async for event in raw_events:
            match event["type"]:
                # ── Thinking: 透传 delta，前端逐字渲染 ──
                case "thinking_start":
                    yield {"type": "thinking", "status": "start"}
                case "thinking_delta":
                    yield {"type": "thinking", "content": event["content"], "streaming": True}
                case "thinking_end":
                    yield {"type": "thinking", "status": "end",
                           "duration_ms": event.get("duration_ms")}

                # ── Plan: 转换结构 ──
                case "plan_created":
                    yield {"type": "plan_checklist",
                           "steps": self._transform_plan(event)}
                case "plan_step_updated":
                    yield {"type": "plan_checklist_update",
                           "step_index": event.get("step_index"),
                           "status": event.get("status")}

                # ── Tool Call: 过滤 → 聚合 → 生成 step_card ──
                case "tool_call_start":
                    if self.tool_filter.should_show(event.get("tool", "")):
                        card = await self.aggregator.process_start(event, self.card_mapper)
                        if card:
                            yield {"type": "step_card", **card}
                case "tool_call_end":
                    if self.tool_filter.should_show(event.get("tool", "")):
                        cards = await self.aggregator.process_end(event, self.card_mapper)
                        for card in cards:
                            yield {"type": "step_card", **card}

                # ── Text: 重命名为 ai_text ──
                case "text_delta":
                    if self._ttft_ms is None:
                        self._ttft_ms = (time.time() - self._start_time) * 1000
                    yield {"type": "ai_text",
                           "content": event["content"], "streaming": True}

                # ── Done: 附加计时信息 ──
                case "done":
                    total_ms = (time.time() - self._start_time) * 1000
                    yield {"type": "done",
                           "usage": event.get("usage"),
                           "timers": {
                               "ttft_ms": round(self._ttft_ms or total_ms),
                               "total_ms": round(total_ms),
                           }}

                # ── 其他事件：静默丢弃（ask_user 等按需添加）──
                case _:
                    pass
```

### 4.3 ToolFilter — 工具过滤规则

```python
class ToolFilter:
    """配置化的工具过滤器：决定哪些工具调用应展示为 step_card。"""

    # 用户可理解的语义动作 → 展示
    SHOW_PATTERNS: list[str] = [
        "web_search", "browser_*", "generate_report", "analyze_data",
        "translate_text", "summarize_*", "create_*", "extract_*",
        "code_execute", "python_execute", "shell_execute",
        "send_email", "send_message",
    ]

    # 系统级/框架级工具 → 隐藏
    HIDE_PATTERNS: list[str] = [
        "read_file", "write_file", "list_files",        # 文件系统内部操作
        "memory_*", "core_memory_*",                     # 记忆读写
        "prompt_*", "context_*",                         # prompt 组装
        "skill_*", "route_*",                            # 框架调度
        "system_config", "get_capabilities",             # 系统配置
    ]

    def should_show(self, tool_name: str) -> bool:
        """判断工具是否应展示为 step_card。"""
        ...
```

### 4.4 StepAggregator — 步骤聚合器

```python
class StepAggregator:
    """
    将连续同类工具调用聚合为单个语义步骤。

    聚合条件：
    1. 连续调用同一工具（如多次 web_search）
    2. 调用间隔 < 阈值（默认 5s）
    3. 满足聚合规则

    聚合后的标题由 LLM 异步生成：
    - 先下发临时标题（如 "搜索中... (3 次)"）
    - LLM 生成后通过 step_card_update 事件更新
    """

    AGGREGATE_WINDOW_SEC = 5.0

    async def process_start(self, event: dict, mapper: CardTypeMapper) -> dict | None:
        """处理 tool_call_start 事件，返回 step_card 或 None（被聚合）。"""
        ...

    async def process_end(self, event: dict, mapper: CardTypeMapper) -> list[dict]:
        """处理 tool_call_end 事件，返回 step_card 列表。"""
        ...

    async def _generate_semantic_title(self, tool_name: str, args_list: list[dict]) -> str:
        """用小模型生成聚合步骤的语义标题。"""
        # 示例 prompt: "以下是 3 次搜索调用的参数，请用一句话概括用户意图："
        # 输出: "搜索 Karpathy 2026 年最新 AI Agent 观点"
        ...
```

### 4.5 CardTypeMapper — 卡片类型映射

```python
class CardTypeMapper:
    """根据 tool_name 自动推断 card_type，无需 LLM。"""

    MAPPING: dict[str, str] = {
        # search
        "web_search": "search",
        "search_*": "search",

        # code
        "code_execute": "code",
        "python_execute": "code",
        "shell_execute": "code",

        # file
        "generate_report": "file",
        "create_document": "file",
        "export_*": "file",

        # analysis
        "analyze_data": "analysis",
        "chart_*": "analysis",

        # browser
        "browser_*": "browser",
        "navigate_*": "browser",
    }

    def get_type(self, tool_name: str) -> str:
        """返回 card_type，未匹配返回 'default'。"""
        ...
```

**card_type 类型表**：

| card_type | 触发工具 | 卡片图标 | 示例标题 |
|-----------|---------|---------|---------|
| `default` | 未匹配 | 齿轮 ⚙ | 通用工具调用 |
| `search` | web_search 等 | 搜索 🔍 | 搜索 Karpathy 最新观点 |
| `code` | code/python/shell_execute | 代码 </> | 执行 Python 数据处理脚本 |
| `file` | generate_report, export_* | 文件 📄 | 生成 PDF 报告 |
| `analysis` | analyze_data, chart_* | 图表 📊 | 分析销售趋势数据 |
| `browser` | browser_*, navigate_* | 浏览器 🌐 | 浏览目标网页 |

---

## 5. 后端：SSE 端点设计

### 5.1 路由注册

```
src/openakita/api/routes/seecrab.py   ← 新增路由文件
```

### 5.2 端点定义

```
POST /api/seecrab/chat
Content-Type: application/json
Accept: text/event-stream
```

请求体与现有 `/api/chat` 共享 `ChatRequest` schema，复用 Agent 实例池、Session 管理、busy-lock 等机制。

### 5.3 SSE 事件流协议

#### 普通模式（Normal Mode）

```
前端                              后端
 │                                 │
 │  POST /api/seecrab/chat         │
 │  { "message": "..." }           │
 │ ──────────────────────────────→ │
 │                                 │
 │  SSE: thinking (streaming)      │  ← 推理过程（逐字流式）
 │ ←────────────────────────────── │
 │                                 │
 │  SSE: step_card (running)       │  ← 步骤开始
 │ ←────────────────────────────── │
 │                                 │
 │  SSE: step_card (completed)     │  ← 步骤完成（含 I/O）
 │ ←────────────────────────────── │
 │                                 │
 │  SSE: ai_text (streaming)       │  ← 最终输出（逐字流式）
 │ ←────────────────────────────── │
 │                                 │
 │  SSE: done                      │  ← 流结束 + 计时 + usage
 │ ←────────────────────────────── │
```

#### 计划模式（Plan Mode）

```
前端                              后端
 │                                 │
 │  POST /api/seecrab/chat         │
 │ ──────────────────────────────→ │
 │                                 │
 │  SSE: thinking (streaming)      │  ← 推理过程
 │ ←────────────────────────────── │
 │                                 │
 │  SSE: plan_checklist            │  ← 计划清单（全部 pending）
 │ ←────────────────────────────── │
 │                                 │
 │  SSE: step_card (running)       │  ← 步骤 1 开始
 │ ←────────────────────────────── │
 │  SSE: plan_checklist_update     │  ← 清单更新：步骤 1 running
 │ ←────────────────────────────── │
 │                                 │
 │  SSE: step_card (completed)     │  ← 步骤 1 完成
 │ ←────────────────────────────── │
 │  SSE: plan_checklist_update     │  ← 清单更新：步骤 1 ☑
 │ ←────────────────────────────── │
 │                                 │
 │  ...重复直到全部步骤完成...       │
 │                                 │
 │  SSE: ai_text (streaming)       │  ← 最终总结
 │ ←────────────────────────────── │
 │                                 │
 │  SSE: done                      │  ← 流结束
 │ ←────────────────────────────── │
```

### 5.4 事件体结构

```json
// thinking（流式）
{"type": "thinking", "content": "用户想了解...", "streaming": true}
{"type": "thinking", "status": "end", "duration_ms": 1200}

// plan_checklist
{"type": "plan_checklist", "steps": [
  {"index": 1, "title": "搜索 Karpathy 最新观点", "status": "pending"},
  {"index": 2, "title": "整理要点并归类", "status": "pending"},
  {"index": 3, "title": "生成摘要报告", "status": "pending"}
]}

// plan_checklist_update
{"type": "plan_checklist_update", "step_index": 1, "status": "completed"}

// step_card（运行中）
{"type": "step_card", "step_id": "s_001", "title": "搜索 Karpathy 2026 最新观点",
 "status": "running", "card_type": "search"}

// step_card（完成，含 I/O）
{"type": "step_card", "step_id": "s_001", "title": "搜索 Karpathy 2026 最新观点",
 "status": "completed", "card_type": "search", "duration": 3.2,
 "input": {"query": "Karpathy 2026", "max_results": 5},
 "output": "搜索结果: 1. Karpathy 在 2026 年..."}

// step_card_update（聚合标题更新）
{"type": "step_card_update", "step_id": "s_001",
 "title": "搜索 Karpathy 2026 年最新 AI Agent 观点"}

// ai_text（流式）
{"type": "ai_text", "content": "根据搜索结果，", "streaming": true}

// done（含会话标题）
{"type": "done", "usage": {"input_tokens": 1200, "output_tokens": 800},
 "timers": {"ttft_ms": 1200, "total_ms": 4800},
 "session_title": "Karpathy 2026 AI Agent 观点摘要"}
```

### 5.5 会话标题自动生成

左侧会话列表的标题由 LLM 异步生成，流程如下：

```
用户发送第一条消息
      │
      ▼
后端正常处理对话，SSE 流式返回
      │
      ▼
done 事件发出后，后端异步触发标题生成（不阻塞 SSE 流）
      │
      ▼
用小模型总结本轮对话生成 ≤15 字标题
      │
      ▼
通过 done 事件的 session_title 字段返回
或通过 WebSocket 广播 session:title_updated 事件
```

#### 生成策略

| 场景 | 行为 |
|------|------|
| 新会话第一轮 | 根据用户消息 + AI 回复生成标题 |
| 已有标题的后续对话 | 不重新生成（保持稳定） |
| 用户手动重命名 | 优先使用用户命名，不再覆盖 |

#### 实现方式

在 `SeeCrabAdapter` 的 `done` 事件处理中，判断是否需要生成标题：

```python
# seecrab_adapter.py — done 事件处理
case "done":
    total_ms = (time.time() - self._start_time) * 1000
    done_data = {
        "usage": event.get("usage"),
        "timers": {
            "ttft_ms": round(self._ttft_ms or total_ms),
            "total_ms": round(total_ms),
        },
    }

    # 异步生成会话标题（仅新会话首轮）
    if self._is_first_turn and self._brain:
        title = await self._generate_session_title(
            user_message=self._user_message,
            ai_reply_preview=self._ai_text_preview,
        )
        if title:
            done_data["session_title"] = title

    yield {"type": "done", **done_data}
```

标题生成 prompt 示例：

```
请用不超过 15 个字总结以下对话的主题，直接输出标题，不要引号：
用户：搜索 Karpathy 最新观点并写一篇摘要
AI：根据搜索结果，Karpathy 最近在 2026 年 3 月发表了关于 AI Agent 的...
```

#### 前端处理

```typescript
// useSSEChat.ts — 收到 done 事件
case "done":
  if (data.session_title) {
    chatStore.updateConversationTitle(conversationId, data.session_title);
  }
  break;
```

---

## 6. 前端：SeeCrab React App

### 6.1 技术栈

| 技术 | 选型 | 说明 |
|------|------|------|
| 框架 | React 19 + TypeScript | 组件化开发 |
| 构建 | Vite 6 | 快速 HMR |
| 状态 | Zustand | 轻量状态管理 |
| 样式 | Tailwind CSS | 暗色主题，与 demo 风格一致 |
| Markdown | react-markdown + remark-gfm | 总结输出渲染 |
| 代码高亮 | highlight.js 或 Shiki | 代码块渲染 |
| 图标 | Material Symbols | 与 demo 保持一致 |

### 6.2 项目结构

```
apps/seecrab/
  package.json
  vite.config.ts
  tailwind.config.ts
  index.html
  public/
  src/
    main.tsx                          ← 入口
    App.tsx                           ← 根组件 + 路由
    types/
      events.ts                       ← SSE 事件类型定义
      chat.ts                         ← 消息/会话类型
    hooks/
      useSSEChat.ts                   ← SSE 连接 + 事件解析
      useChatStore.ts                 ← Zustand 聊天状态
      useTimers.ts                    ← TTFT/Total 计时器
    components/
      Layout/
        AppLayout.tsx                 ← 两栏/三栏响应式布局
        Header.tsx                    ← 顶部栏
      Sidebar/
        Sidebar.tsx                   ← 左侧栏容器
        NewChatButton.tsx             ← + 新对话按钮
        SessionList.tsx               ← 会话列表
        SessionItem.tsx               ← 单个会话项
      ChatArea/
        ChatArea.tsx                  ← 中间聊天区容器
        MessageFlow.tsx               ← 消息流滚动容器
        UserMessage.tsx               ← 用户消息气泡
        BotReply.tsx                  ← 机器人回复（自适应三段/四段式）
        ThinkingBlock.tsx             ← 折叠的 Thinking 区（实时流式渲染）
        PlanChecklist.tsx             ← 计划清单（可折叠，checkbox 自动更新）
        StepCard.tsx                  ← 步骤卡片（条状，按 card_type 渲染图标）
        SummaryOutput.tsx             ← Markdown 总结输出（流式渲染）
        TimerDisplay.tsx              ← TTFT + Total 计时器（运行态/完成态）
        InputBar.tsx                  ← 输入框 + 发送按钮
      RightPanel/
        RightPanel.tsx                ← 右侧面板容器（样式 A）
        StepDetail.tsx                ← 步骤 I/O 详情
        StepInput.tsx                 ← 输入展示（JSON 渲染）
        StepOutput.tsx                ← 输出展示（Markdown 渲染）
      Welcome/
        WelcomePage.tsx               ← 新对话 Welcome 页
        QuickAction.tsx               ← 快捷操作按钮
    styles/
      globals.css                     ← 全局样式 + CSS 变量（暗色主题）
```

### 6.3 核心 Hook: useSSEChat

```typescript
interface UseSSEChatReturn {
  // 状态
  messages: ChatMessage[];
  isStreaming: boolean;
  timers: { ttft_ms: number | null; total_ms: number | null };
  error: string | null;

  // 操作
  sendMessage: (content: string) => void;
  cancelStream: () => void;
}

function useSSEChat(conversationId: string): UseSSEChatReturn {
  // POST /api/seecrab/chat → SSE 流
  // 解析 5 种事件类型，更新 Zustand store
  // 自动管理连接生命周期
}
```

### 6.4 状态管理: useChatStore

```typescript
interface ChatStore {
  // 会话
  conversations: Conversation[];       // title 初始为 "新对话"，done 事件后更新
  activeConversationId: string | null;

  // 当前对话消息
  messages: ChatMessage[];

  // 当前回复状态（流式构建中）
  currentReply: {
    thinking: string;          // 累积的 thinking 内容
    thinkingDone: boolean;
    planChecklist: PlanStep[] | null;
    stepCards: StepCard[];
    summaryText: string;       // 累积的 ai_text 内容
    timers: Timers;
  } | null;

  // 右侧面板
  selectedStepId: string | null;  // 当前展开的步骤

  // Actions
  appendThinking: (content: string) => void;
  setPlanChecklist: (steps: PlanStep[]) => void;
  updatePlanStep: (index: number, status: string) => void;
  addOrUpdateStepCard: (card: StepCard) => void;
  appendSummary: (content: string) => void;
  finalizeReply: (timers: Timers, usage: Usage) => void;
  selectStep: (stepId: string | null) => void;
  updateConversationTitle: (convId: string, title: string) => void;
  renameConversation: (convId: string, title: string) => void;  // 用户手动重命名
}
```

### 6.5 BotReply 自适应渲染逻辑

```
收到消息流 →
  ├─ 有 plan_checklist？
  │   ├─ 是 → 四段式渲染
  │   │   ├─ ThinkingBlock (折叠)
  │   │   ├─ PlanChecklist (展开)
  │   │   ├─ StepCard[] (逐步出现)
  │   │   └─ SummaryOutput (最后)
  │   └─ 否 → 三段式渲染
  │       ├─ ThinkingBlock (折叠)
  │       ├─ StepCard[] (0~N 个)
  │       └─ SummaryOutput
  └─ TimerDisplay 始终显示在头部
```

### 6.6 StepCard 按 card_type 渲染

```typescript
const CARD_TYPE_CONFIG: Record<string, { icon: string; color: string }> = {
  default:  { icon: "settings",       color: "text-slate-400" },
  search:   { icon: "search",         color: "text-blue-400" },
  code:     { icon: "code",           color: "text-green-400" },
  file:     { icon: "description",    color: "text-orange-400" },
  analysis: { icon: "bar_chart",      color: "text-purple-400" },
  browser:  { icon: "language",       color: "text-cyan-400" },
};
```

---

## 7. 通信方案

### 7.1 选型：单 SSE 连接

| 方案 | 优劣 | 结论 |
|------|------|------|
| SSE (Server-Sent Events) | 匹配请求-响应模式，复用成熟基础设施，浏览器原生支持 | **采用** |
| WebSocket | 双向通信在聊天场景无必要，增加复杂度 | 不采用 |
| 长轮询 | 延迟高，资源浪费 | 不采用 |

**用户输入用 POST，AI 输出用 SSE。**

### 7.2 TTFT / Total 计时方案

| 指标 | 计算方式 | 展示 |
|------|---------|------|
| TTFT | 后端：从收到请求到第一个 `text_delta` 的时间差 | 运行态：蓝色脉冲 + 计时中；完成态：灰色静态 |
| Total | 后端：从收到请求到 `done` 事件的时间差 | 同上 |

后端在 `done` 事件中返回 `timers.ttft_ms` 和 `timers.total_ms`。
前端同时自行维护计时器做 UI 动画（收到 `done` 后用后端值覆盖）。

### 7.3 右侧面板数据流

```
用户点击 step_card 的 → 箭头
      │
      ▼
前端从 Zustand store 中
读取该 step_id 的 input/output
      │
      ▼
渲染右侧面板（样式 A）
┌──────────────────────┐
│ title + status       │
│ input (JSON 渲染)     │
│ output (Markdown)    │
└──────────────────────┘
      │
如果 status == "running"
      │
      ▼
store 中该 step_card 被
后续 SSE 更新时，面板自动刷新
```

无需额外 API 请求，所有数据来自 SSE 事件中的 `step_card` 消息体。

---

## 8. 文件变更范围

### 8.1 后端（Python）

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/openakita/api/adapters/__init__.py` | 新增 | 模块初始化 |
| `src/openakita/api/adapters/seecrab_adapter.py` | 新增 | 核心协议适配器 |
| `src/openakita/api/adapters/tool_filter.py` | 新增 | 工具过滤规则（配置化） |
| `src/openakita/api/adapters/step_aggregator.py` | 新增 | 步骤聚合器 + LLM 标题生成 |
| `src/openakita/api/adapters/card_type_mapper.py` | 新增 | tool_name → card_type 映射 |
| `src/openakita/api/routes/seecrab.py` | 新增 | SeeCrab SSE 端点 |
| `src/openakita/api/server.py` | 修改 | 注册 SeeCrab 路由 |

### 8.2 前端（React）

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `apps/seecrab/` | 新增 | 整个前端项目 |

### 8.3 不修改的文件

以下核心模块**零改动**：

- `src/openakita/core/reasoning_engine.py`
- `src/openakita/core/agent.py`
- `src/openakita/core/brain.py`
- `src/openakita/core/tool_executor.py`
- `src/openakita/api/routes/chat.py`

---

## 9. 设计决策总结

| 决策项 | 选择 | 理由 |
|-------|------|------|
| 事件协议 | 后端适配层（不改引擎） | 引擎保持通用，UI 协议独立演进 |
| 通信方式 | 单 SSE 连接 | 匹配请求-响应模式，复用成熟基础设施 |
| 工具过滤 | 后端配置化规则 | 前端保持"笨客户端"，便于维护 |
| 步骤聚合 | 后端实时聚合 | 前端不需要知道聚合逻辑 |
| 聚合标题 | LLM 异步生成 | 语义准确，先下发临时标题再更新 |
| card_type | 后端根据 tool_name 规则推断 | 零 LLM 开销，确定性映射 |
| 前端框架 | 独立 React + Vite（纯 Web） | 与 setup-center 解耦，独立部署 |
| Thinking 渲染 | 实时流式（逐字追加） | 用户可实时看到推理过程 |
| 会话标题 | LLM 异步生成，done 事件返回 | 新会话首轮自动命名，不阻塞流式输出 |
| 部署形态 | 纯 Web 部署 | 浏览器直接访问，无需桌面打包 |
