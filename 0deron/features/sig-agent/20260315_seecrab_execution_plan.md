# SeeCrab 执行计划

> 基于架构设计方案 `20260315_seecrab_architecture.md` 的分阶段执行计划。
> 日期：2026-03-15

---

## 执行原则

- **自底向上构建**：先后端适配层 → SSE 端点 → 前端骨架 → 组件实现 → 联调
- **每个阶段可独立验证**：每步完成后有明确的验证手段
- **引擎零改动**：所有变更限制在 `api/adapters/`、`api/routes/seecrab.py` 和 `apps/seecrab/`
- **参考现有模式**：SeeCrab 路由复用 `chat.py` 的 busy-lock、session 管理、断连检测模式

---

## Phase 1：后端适配层（Backend Adapter Layer）

> 目标：将 `ReasoningEngine.reason_stream()` 的 50+ 原始事件转换为 5 种 SeeCrab UI 事件。

### Step 1.1：CardTypeMapper — 卡片类型映射

**文件**: `src/openakita/api/adapters/card_type_mapper.py`

**实现内容**:
- `CardTypeMapper` 类，`MAPPING: dict[str, str]` 存储 tool_name 模式 → card_type 映射
- `get_type(tool_name: str) -> str` 方法，支持通配符匹配（`browser_*` → `"browser"`），未匹配返回 `"default"`
- 支持的 card_type：`default`、`search`、`code`、`file`、`analysis`、`browser`

**数据参考**（架构文档 4.5 节）:
```python
MAPPING = {
    "web_search": "search", "search_*": "search",
    "code_execute": "code", "python_execute": "code", "shell_execute": "code",
    "generate_report": "file", "create_document": "file", "export_*": "file",
    "analyze_data": "analysis", "chart_*": "analysis",
    "browser_*": "browser", "navigate_*": "browser",
}
```

**验证**: 单元测试 — 验证各 tool_name 返回正确的 card_type，通配符匹配正确，未知工具返回 `"default"`。

---

### Step 1.2：ToolFilter — 工具过滤规则

**文件**: `src/openakita/api/adapters/tool_filter.py`

**实现内容**:
- `ToolFilter` 类，维护 `SHOW_PATTERNS` 和 `HIDE_PATTERNS` 两个列表
- `should_show(tool_name: str) -> bool`：白名单优先 → 黑名单匹配 → 默认展示
- 支持 `fnmatch` 风格通配符

**过滤规则**（架构文档 4.3 节 + UI 规范第 5 节）:
- **展示**：`web_search`、`browser_*`、`generate_report`、`analyze_data`、`code_execute`、`python_execute`、`shell_execute`、`translate_text`、`summarize_*`、`create_*`、`extract_*`、`send_email`、`send_message`
- **隐藏**：`read_file`、`write_file`、`list_files`、`memory_*`、`core_memory_*`、`prompt_*`、`context_*`、`skill_*`、`route_*`、`system_config`、`get_capabilities`

**验证**: 单元测试 — 各工具名的展示/隐藏判定正确。

---

### Step 1.3：StepAggregator — 步骤聚合器

**文件**: `src/openakita/api/adapters/step_aggregator.py`

**实现内容**:
- `StepAggregator` 类，状态化处理连续工具调用的聚合
- 内部状态：`_current_group: list[dict]`（当前聚合组）、`_last_tool: str`、`_last_time: float`、`_group_step_id: str`
- `process_start(event, card_mapper) -> dict | None`：
  - 如果与前一个调用同工具且间隔 < 5s → 聚合（返回 None，更新内部计数）
  - 否则 → flush 前一组，开始新组，返回新 step_card（status=running）
- `process_end(event, card_mapper) -> list[dict]`：
  - 返回 step_card（status=completed，含 input/output/duration）
  - 如果是聚合组的最后一个 → 触发 LLM 标题生成，返回 `step_card_update`
- `_generate_semantic_title(tool_name, args_list) -> str`：
  - 用 `brain.call_llm()` 调用小模型生成 ≤15 字语义标题
  - prompt：汇总多次调用参数，要求一句话概括用户意图
  - 超时兜底：返回 `"{tool_name} ({count} 次)"`
- `flush() -> list[dict]`：强制输出当前聚合组（用于流结束时调用）

**关键设计**:
- step_id 生成：`f"s_{uuid4().hex[:8]}"`
- 聚合窗口：`AGGREGATE_WINDOW_SEC = 5.0`
- 临时标题格式：`"搜索中... (3 次)"` → LLM 生成后通过 `step_card_update` 更新
- `brain` 参数可选：无 brain 时退化为规则模板标题

**验证**: 单元测试 — 单工具不聚合、连续同类工具聚合、超时不聚合、flush 行为正确。

---

### Step 1.4：SeeCrabAdapter — 核心适配器

**文件**: `src/openakita/api/adapters/seecrab_adapter.py`

**实现内容**:
- `SeeCrabAdapter` 类，组合 `ToolFilter`、`StepAggregator`、`CardTypeMapper`
- `async def adapt(raw_events: AsyncIterator[dict]) -> AsyncIterator[dict]`：
  - 消费 `reason_stream()` 原始事件，产出 SeeCrab UI 事件
  - 事件映射（架构文档 4.2 节）：
    - `thinking_start` → `{"type": "thinking", "status": "start"}`
    - `thinking_delta` → `{"type": "thinking", "content": ..., "streaming": True}`
    - `thinking_end` → `{"type": "thinking", "status": "end", "duration_ms": ...}`
    - `plan_created` → `{"type": "plan_checklist", "steps": [...]}`
    - `plan_step_updated` → `{"type": "plan_checklist_update", "step_index": ..., "status": ...}`
    - `tool_call_start` → 过滤 + 聚合 → `{"type": "step_card", ...}` 或吞掉
    - `tool_call_end` → 过滤 + 聚合 → `{"type": "step_card", ...}`
    - `text_delta` → `{"type": "ai_text", "content": ..., "streaming": True}`
    - `done` → `{"type": "done", "usage": ..., "timers": {...}, "session_title": ...}`
    - 其他事件 → 静默丢弃
- TTFT 计时：记录第一个 `text_delta` 的时间戳
- Total 计时：`done` 事件时计算总耗时
- `_transform_plan(event) -> list[dict]`：将 `plan_created` 事件的 plan 结构转换为 `[{index, title, status}]` 格式
- 会话标题生成（架构文档 5.5 节）：
  - `_is_first_turn: bool`（由外部传入或根据 session 消息数判断）
  - `_user_message: str`（累积用户消息）
  - `_ai_text_preview: str`（累积前 200 字 ai_text）
  - 在 `done` 事件时，如果 `_is_first_turn` 且有 `brain`，调用小模型生成标题
  - 标题写入 `done_data["session_title"]`

**依赖**: Step 1.1 ~ 1.3 的三个模块

**验证**: 集成测试 — 用 mock 事件流验证完整转换链路，包括 thinking → step_card → ai_text → done 的完整流程。

---

### Step 1.5：`__init__.py` 模块导出

**文件**: `src/openakita/api/adapters/__init__.py`

**实现内容**:
```python
from .seecrab_adapter import SeeCrabAdapter
from .tool_filter import ToolFilter
from .card_type_mapper import CardTypeMapper
from .step_aggregator import StepAggregator

__all__ = ["SeeCrabAdapter", "ToolFilter", "CardTypeMapper", "StepAggregator"]
```

---

## Phase 2：后端 SSE 端点（Backend Route）

> 目标：创建 `/api/seecrab/chat` SSE 端点，复用现有 Agent 流水线 + SeeCrabAdapter 转换。

### Step 2.1：SeeCrab 路由实现

**文件**: `src/openakita/api/routes/seecrab.py`

**实现内容** — 参考 `chat.py` 的模式，但用 `SeeCrabAdapter` 包装事件流：

```python
router = APIRouter()

@router.post("/api/seecrab/chat")
async def seecrab_chat(request: Request, body: ChatRequest):
    ...
```

**复用 `chat.py` 的基础设施**：
- `ChatRequest` schema（从 `schemas.py` 导入，无需修改）
- busy-lock 机制（`_mark_busy` / `_clear_busy`，独立实例，SeeCrab 端点用自己的 lock 字典）
- `_get_agent_for_session()`（复用 AgentInstancePool）
- Session 管理（`session_manager.get_session(channel="seecrab", ...)`）
- 断连检测（`_disconnect_watcher` 协程模式）
- `engine_stream()` 桥接（双事件循环支持）
- WebSocket 广播（`chat:busy` / `chat:idle`）

**核心区别**：在 `_stream_chat` 等价函数中，用 `SeeCrabAdapter.adapt()` 包装 `agent.chat_with_session_stream()` 的输出：

```python
async def _stream_seecrab(chat_request, agent, session_manager, http_request):
    adapter = SeeCrabAdapter(brain=actual_agent.brain, is_first_turn=is_first_turn)

    raw_stream = actual_agent.chat_with_session_stream(
        message=chat_request.message or "",
        session_messages=session_messages_history,
        session_id=conversation_id,
        session=session,
        ...
    )

    async for ui_event in adapter.adapt(raw_stream):
        if await _check_disconnected():
            actual_agent.cancel_current_task("客户端断开连接", session_id=conversation_id)
            break
        yield _sse(ui_event["type"], {k: v for k, v in ui_event.items() if k != "type"})
```

**Session 保存**：
- 复用 `chat.py` 的 session 保存逻辑（保存完整回复文本到 session）
- 如果 `done` 事件包含 `session_title`，同时更新 session 的标题

**验证**: 手动 curl 测试 — 发送 POST 请求，验证返回 SSE 流包含正确的事件类型。

---

### Step 2.2：路由注册

**文件**: `src/openakita/api/server.py`

**修改内容**:
- 在 route 导入区域添加 `from .routes import seecrab`
- 在 `app.include_router()` 区域添加 `app.include_router(seecrab.router, tags=["SeeCrab"])`

**验证**: 启动服务，访问 `/docs` 确认新端点出现在 API 文档中。

---

### Step 2.3：后端单元测试

**文件**: `tests/unit/test_seecrab_adapter.py`

**测试用例**:
1. `test_card_type_mapper_exact_match` — 精确匹配 tool_name
2. `test_card_type_mapper_wildcard` — 通配符匹配
3. `test_card_type_mapper_default` — 未知工具返回 default
4. `test_tool_filter_show` — 展示类工具通过
5. `test_tool_filter_hide` — 系统工具过滤
6. `test_step_aggregator_no_aggregate` — 单次调用不聚合
7. `test_step_aggregator_merge` — 连续同类调用聚合
8. `test_step_aggregator_timeout_breaks` — 超时打断聚合
9. `test_adapter_thinking_events` — thinking 事件转换
10. `test_adapter_text_events` — text_delta → ai_text
11. `test_adapter_plan_events` — plan 事件转换
12. `test_adapter_tool_filter_integration` — 过滤后的 step_card 输出
13. `test_adapter_timers` — TTFT / Total 计时正确
14. `test_adapter_done_with_session_title` — done 事件包含会话标题

**验证**: `pytest tests/unit/test_seecrab_adapter.py -x -v`

---

## Phase 3：前端项目初始化（Frontend Scaffold）

> 目标：搭建 SeeCrab React 项目骨架，可运行的空壳 + 暗色主题 + 基础布局。

### Step 3.1：项目初始化

**目录**: `apps/seecrab/`

**操作**:
1. 使用 Vite 创建 React + TypeScript 项目
2. 安装依赖：
   - `react@^19` + `react-dom@^19`
   - `zustand` — 状态管理
   - `react-markdown` + `remark-gfm` — Markdown 渲染
   - `highlight.js` — 代码高亮
3. 配置 Tailwind CSS v4（参考 setup-center 的 `@tailwindcss/vite` 方式）
4. 配置 Vite proxy：`/api` → `http://localhost:8080`（开发时代理到后端）

**产出文件**:
```
apps/seecrab/
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/
    main.tsx
    App.tsx
    styles/globals.css    ← CSS 变量（暗色主题，与 demo 一致）
```

**CSS 变量**（参考 demo HTML `:root`）:
```css
:root {
  --bg-dark: #0f172a;
  --bg-card: #1e293b;
  --bg-card-hover: #273549;
  --primary: #3b82f6;
  --primary-dim: rgba(59,130,246,0.15);
  --text: #e2e8f0;
  --text-dim: #92a4c9;
  --text-muted: #64748b;
  --border: rgba(59,130,246,0.1);
  --green: #34d399;
  --red: #f87171;
  --sidebar-w: 260px;
  --right-panel-w: 380px;
}
```

**验证**: `cd apps/seecrab && npm run dev` — 浏览器能打开空页面，暗色背景。

---

### Step 3.2：TypeScript 类型定义

**文件**: `apps/seecrab/src/types/events.ts`

**内容** — 所有 SSE 事件的类型定义：
```typescript
// SSE 事件联合类型
type SSEEvent = ThinkingEvent | PlanChecklistEvent | PlanChecklistUpdateEvent
  | StepCardEvent | StepCardUpdateEvent | AITextEvent | DoneEvent | ErrorEvent;

interface ThinkingEvent { type: "thinking"; content?: string; status?: "start" | "end"; streaming?: boolean; duration_ms?: number; }
interface PlanChecklistEvent { type: "plan_checklist"; steps: PlanStep[]; }
interface PlanChecklistUpdateEvent { type: "plan_checklist_update"; step_index: number; status: string; }
interface StepCardEvent { type: "step_card"; step_id: string; title: string; status: "running" | "completed" | "failed"; card_type: string; duration?: number; input?: Record<string, any>; output?: string; }
interface StepCardUpdateEvent { type: "step_card_update"; step_id: string; title: string; }
interface AITextEvent { type: "ai_text"; content: string; streaming: boolean; }
interface DoneEvent { type: "done"; usage?: Usage; timers?: Timers; session_title?: string; }
interface ErrorEvent { type: "error"; message: string; }
```

**文件**: `apps/seecrab/src/types/chat.ts`

**内容** — 消息与会话模型：
```typescript
interface Conversation { id: string; title: string; isUserRenamed: boolean; createdAt: number; updatedAt: number; }
interface ChatMessage { id: string; role: "user" | "assistant"; content: string; thinking?: string; planChecklist?: PlanStep[]; stepCards?: StepCard[]; timers?: Timers; timestamp: number; }
interface PlanStep { index: number; title: string; status: "pending" | "running" | "completed" | "failed"; }
interface StepCard { step_id: string; title: string; status: "running" | "completed" | "failed"; card_type: string; duration?: number; input?: Record<string, any>; output?: string; }
interface Timers { ttft_ms: number; total_ms: number; }
interface Usage { input_tokens: number; output_tokens: number; total_tokens?: number; }
```

**验证**: TypeScript 编译通过。

---

### Step 3.3：Zustand Store

**文件**: `apps/seecrab/src/hooks/useChatStore.ts`

**实现内容**（架构文档 6.4 节）:
- `conversations: Conversation[]` — 会话列表（localStorage 持久化）
- `activeConversationId: string | null`
- `messages: Map<string, ChatMessage[]>` — 按会话 ID 索引的消息
- `currentReply` — 当前流式构建中的回复状态
- `selectedStepId: string | null` — 右侧面板选中的步骤
- Actions：`createConversation`、`switchConversation`、`deleteConversation`、`appendThinking`、`setPlanChecklist`、`updatePlanStep`、`addOrUpdateStepCard`、`appendSummary`、`finalizeReply`、`selectStep`、`updateConversationTitle`、`renameConversation`
- 使用 `zustand/middleware` 的 `persist` 做 localStorage 持久化（仅 conversations + messages）

**验证**: 单元测试或手动控制台调用 store actions 验证状态变化。

---

### Step 3.4：SSE Hook

**文件**: `apps/seecrab/src/hooks/useSSEChat.ts`

**实现内容**（架构文档 6.3 节）:
- `useSSEChat(conversationId: string)` hook
- 使用 `fetch()` + `ReadableStream` 解析 SSE（不用 EventSource，因为需要 POST）
- 事件解析：`data: {...}\n\n` → JSON parse → dispatch 到 store
- 事件分发逻辑：
  - `thinking` → `store.appendThinking(content)` 或 `store.setThinkingDone()`
  - `plan_checklist` → `store.setPlanChecklist(steps)`
  - `plan_checklist_update` → `store.updatePlanStep(index, status)`
  - `step_card` → `store.addOrUpdateStepCard(card)`
  - `step_card_update` → `store.updateStepCardTitle(step_id, title)`
  - `ai_text` → `store.appendSummary(content)`
  - `done` → `store.finalizeReply(timers, usage)` + `store.updateConversationTitle(convId, session_title)`
  - `error` → `store.setError(message)`
- 取消支持：`AbortController` — `cancelStream()` 调用 `controller.abort()`
- 返回 `{ sendMessage, cancelStream, isStreaming, error }`

**文件**: `apps/seecrab/src/hooks/useTimers.ts`

**实现内容**:
- `useTimers()` hook — 前端自行维护 TTFT / Total 计时器的 UI 动画
- `startTimer()` — 记录开始时间，启动 `requestAnimationFrame` 更新显示值
- `markTTFT()` — 收到第一个 `ai_text` 时锁定 TTFT
- `finalize(serverTimers)` — 用后端返回的精确值覆盖前端计时
- 返回 `{ ttft, total, isRunning }`

**验证**: 用 mock SSE 流测试事件解析和 store 更新。

---

## Phase 4：前端布局组件（Layout & Sidebar）

> 目标：实现两栏/三栏响应式布局 + 左侧栏 + Welcome 页。

### Step 4.1：AppLayout — 响应式布局

**文件**: `apps/seecrab/src/components/Layout/AppLayout.tsx`

**实现内容**:
- Flex 容器：`Sidebar (260px) + ChatArea (flex:1) + RightPanel (380px, 条件渲染)`
- `selectedStepId` 非 null 时展开三栏，否则两栏
- 过渡动画：右侧面板 slide-in/out（`transition: width 0.3s ease`）
- 参考 demo HTML 的 `.layout` 样式

**文件**: `apps/seecrab/src/components/Layout/Header.tsx`

**实现内容**:
- 仅在移动端显示（桌面端左侧栏已有 brand）
- 暂时可为空组件，后续按需添加

**验证**: 浏览器中看到左侧栏 + 中间区域的两栏布局。

---

### Step 4.2：Sidebar — 左侧栏

**文件**:
- `apps/seecrab/src/components/Sidebar/Sidebar.tsx` — 容器
- `apps/seecrab/src/components/Sidebar/NewChatButton.tsx` — + 新对话按钮
- `apps/seecrab/src/components/Sidebar/SessionList.tsx` — 会话列表
- `apps/seecrab/src/components/Sidebar/SessionItem.tsx` — 单个会话项

**实现内容**:
- Brand 区域：logo + "SeeCrab" 文字（参考 demo `.sidebar-brand`）
- `+ 新对话` 按钮：虚线边框，点击调用 `store.createConversation()`
- 会话列表：按 `updatedAt` 倒序，active 项高亮（`primary-dim` 背景）
- 会话项：图标 + 标题（单行省略） + 时间戳
- 标题初始为 "新对话"，`done` 事件后异步更新

**验证**: 能创建新对话，切换对话，列表正确排序。

---

### Step 4.3：WelcomePage — 欢迎页

**文件**:
- `apps/seecrab/src/components/Welcome/WelcomePage.tsx`
- `apps/seecrab/src/components/Welcome/QuickAction.tsx`

**实现内容**（UI 规范 1.4 节）:
- 居中布局：Logo + "你好，我是 OpenAkita" + 副标题
- 4 个快捷操作按钮（2×2 网格）：网络搜索、代码助手、文档处理、数据分析
- 点击按钮 → 输入框预填文本 + 聚焦
- 当 `messages.length === 0` 时显示 Welcome，有消息后切换到 MessageFlow

**验证**: 新对话显示 Welcome 页，点击快捷按钮预填输入框。

---

### Step 4.4：InputBar — 输入框

**文件**: `apps/seecrab/src/components/ChatArea/InputBar.tsx`

**实现内容**:
- 底部固定输入框 + 发送按钮
- `textarea` 自适应高度（shift+enter 换行，enter 发送）
- 流式输出时禁用发送，显示"停止生成"按钮（调用 `cancelStream()`）
- 参考 demo `.chat-input-area` 样式

**验证**: 输入文字，按 Enter 发送，流式时显示停止按钮。

---

## Phase 5：前端聊天核心组件（Chat Components）

> 目标：实现消息流渲染，包括三段式/四段式回复结构。

### Step 5.1：MessageFlow + UserMessage

**文件**:
- `apps/seecrab/src/components/ChatArea/ChatArea.tsx` — 容器
- `apps/seecrab/src/components/ChatArea/MessageFlow.tsx` — 滚动容器
- `apps/seecrab/src/components/ChatArea/UserMessage.tsx` — 用户消息

**实现内容**:
- `MessageFlow`：`overflow-y: auto`，新消息自动滚动到底部（`scrollIntoView`）
- `UserMessage`：简单文本显示，右对齐或左对齐均可（参考 demo）
- 遍历 `messages` + `currentReply`（如果正在流式）

**验证**: 发送消息后显示用户消息，自动滚动。

---

### Step 5.2：TimerDisplay — TTFT/Total 计时器

**文件**: `apps/seecrab/src/components/ChatArea/TimerDisplay.tsx`

**实现内容**（UI 规范 2.1 节）:
- 头像 + "OpenAkita" + TTFT + Total 一行显示
- 运行态：蓝色脉冲圆点（CSS `@keyframes pulse`）+ 数字递增
- 完成态：灰色静态圆点 + 最终值
- 使用 `useTimers` hook 的数据

**验证**: 流式输出时计时器实时递增，完成后静态显示。

---

### Step 5.3：ThinkingBlock — 折叠的推理区

**文件**: `apps/seecrab/src/components/ChatArea/ThinkingBlock.tsx`

**实现内容**（UI 规范 2.2 节）:
- 默认折叠，显示 "▶ Thinking..." + 展开箭头 `↓`
- 点击展开：显示流式追加的 thinking 文本（实时打字机效果）
- thinking 完成后显示 "Thinking (1.2s)"
- 折叠状态切换用 `useState`

**验证**: 流式输出时 thinking 文字实时追加，可折叠/展开。

---

### Step 5.4：PlanChecklist — 计划清单

**文件**: `apps/seecrab/src/components/ChatArea/PlanChecklist.tsx`

**实现内容**（UI 规范 2.3 节）:
- 独立区块，默认展开，支持手动折叠
- 标题："执行计划"
- 每步显示：checkbox（☐/☑）+ 步骤标题 + 状态标记（"进行中"）
- `plan_checklist_update` 事件驱动 checkbox 状态变化
- checkbox 变化动画（CSS transition）

**验证**: Plan mode 下显示清单，步骤状态随 SSE 事件自动更新。

---

### Step 5.5：StepCard — 步骤卡片

**文件**: `apps/seecrab/src/components/ChatArea/StepCard.tsx`

**实现内容**（UI 规范附录 + 架构文档 6.6 节）:
- 条状卡片：`[状态图标] 步骤标题 [→]`
- 状态图标按 status 渲染：
  - `running`：蓝色 spinner 旋转动画
  - `completed`：绿色 ✓
  - `failed`：红色 ✗
- 左侧图标按 `card_type` 渲染（Material Symbols）：
  - `search` → `search` icon (蓝色)
  - `code` → `code` icon (绿色)
  - `file` → `description` icon (橙色)
  - `analysis` → `bar_chart` icon (紫色)
  - `browser` → `language` icon (青色)
  - `default` → `settings` icon (灰色)
- 宽度跟随中间区域（max-w: 720px），高度 40~48px
- 点击 `→` 箭头 → `store.selectStep(step_id)` → 展开右侧面板
- 卡片其他区域不响应点击

**验证**: 不同 card_type 显示不同图标颜色，运行中有 spinner，完成后显示 ✓。

---

### Step 5.6：SummaryOutput — 总结输出

**文件**: `apps/seecrab/src/components/ChatArea/SummaryOutput.tsx`

**实现内容**:
- 使用 `react-markdown` + `remark-gfm` 渲染 Markdown
- 流式追加：`ai_text` 事件逐字追加，react-markdown 实时重新渲染
- 代码块高亮：`highlight.js` 或自定义 `code` 组件
- 完成后可复制全文

**验证**: 流式输出 Markdown 实时渲染，代码块高亮正确。

---

### Step 5.7：BotReply — 机器人回复容器

**文件**: `apps/seecrab/src/components/ChatArea/BotReply.tsx`

**实现内容**（架构文档 6.5 节）:
- 组合 `TimerDisplay` + `ThinkingBlock` + (可选) `PlanChecklist` + `StepCard[]` + `SummaryOutput`
- 自适应三段/四段式：`planChecklist !== null` → 四段式，否则三段式
- 每条机器人回复独立渲染（历史消息 + 当前流式回复）

**验证**: 三段式和四段式回复正确渲染。

---

## Phase 6：右侧面板（Right Panel）

> 目标：实现步骤详情面板（样式 A）。

### Step 6.1：RightPanel + StepDetail

**文件**:
- `apps/seecrab/src/components/RightPanel/RightPanel.tsx` — 面板容器
- `apps/seecrab/src/components/RightPanel/StepDetail.tsx` — 步骤详情
- `apps/seecrab/src/components/RightPanel/StepInput.tsx` — 输入展示
- `apps/seecrab/src/components/RightPanel/StepOutput.tsx` — 输出展示

**实现内容**（UI 规范第 4 节）:
- `RightPanel`：
  - 宽度 380px，条件渲染（`selectedStepId !== null`）
  - Header：标题 "步骤详情" + ✕ 关闭按钮
  - 关闭 → `store.selectStep(null)` → 恢复两栏
- `StepDetail`：
  - 步骤标题 + 状态 + 耗时
  - 输入区（`StepInput`）：JSON 渲染（`JSON.stringify(input, null, 2)` + 语法高亮）
  - 输出区（`StepOutput`）：Markdown 渲染 / 纯文本
- 交互：
  - 点击另一步骤 → 面板内容原地替换（不重新收起展开）
  - 步骤 running 中 → 输出区显示 loading + 实时追加
  - 数据全部来自 store 中对应 `step_id` 的 `StepCard` 对象

**验证**: 点击步骤卡片的 → 箭头展开面板，显示 I/O 详情，关闭恢复两栏。

---

## Phase 7：联调与打磨

> 目标：前后端联调，修复细节，确保完整流程跑通。

### Step 7.1：前后端联调

**操作**:
1. 启动后端：`openakita serve`（或 `python -m openakita serve`）
2. 启动前端：`cd apps/seecrab && npm run dev`（Vite dev server，proxy 到后端）
3. 完整流程测试：
   - 新建对话 → 发送简单问题 → 验证三段式渲染（thinking + ai_text）
   - 发送需要工具调用的问题（如 "搜索 xxx"）→ 验证 step_card 出现 + 右侧面板
   - 发送复杂问题 → 验证四段式渲染（plan_checklist + step_cards）
   - 验证会话标题自动生成
   - 验证 TTFT/Total 计时器
   - 验证 step_card 聚合（连续同类调用）
   - 验证断连后 Agent 取消

### Step 7.2：UI 细节打磨

- 滚动行为：新消息自动滚动，用户手动滚动时停止自动滚动
- 空状态处理：无会话时显示 Welcome
- 错误处理：SSE 错误 → 显示错误提示 + 重试按钮
- 响应式：小屏幕隐藏左侧栏（汉堡菜单切换）
- 深色主题一致性：与 demo 像素级对齐

### Step 7.3：Lint & 质量检查

**后端**:
```bash
ruff check src/openakita/api/adapters/
mypy src/openakita/api/adapters/
pytest tests/unit/test_seecrab_adapter.py -x -v
```

**前端**:
```bash
cd apps/seecrab && npx tsc --noEmit
cd apps/seecrab && npm run build  # 确保生产构建成功
```

---

## 阶段依赖关系

```
Phase 1 (后端适配层)
  ├─ Step 1.1 CardTypeMapper  ─┐
  ├─ Step 1.2 ToolFilter       ├─→ Step 1.4 SeeCrabAdapter → Step 1.5 __init__
  └─ Step 1.3 StepAggregator  ─┘
                                        │
                                        ▼
Phase 2 (后端端点)                    Step 2.1 SeeCrab Route → Step 2.2 注册 → Step 2.3 测试
                                        │
                      ┌─────────────────┤
                      ▼                 ▼
Phase 3 (前端骨架)                 Phase 4 (布局组件)
  Step 3.1 项目初始化               Step 4.1 AppLayout
  Step 3.2 类型定义                 Step 4.2 Sidebar
  Step 3.3 Store                   Step 4.3 Welcome
  Step 3.4 SSE Hook                Step 4.4 InputBar
                      │                 │
                      ▼                 ▼
                Phase 5 (聊天组件)
                  Step 5.1 ~ 5.7
                      │
                      ▼
                Phase 6 (右侧面板)
                  Step 6.1
                      │
                      ▼
                Phase 7 (联调打磨)
                  Step 7.1 ~ 7.3
```

**可并行的步骤**:
- Step 1.1、1.2、1.3 可并行开发
- Phase 3（前端骨架）和 Phase 2（后端端点）可并行开发
- Phase 4 和 Phase 5 前几步可并行（布局 + 消息组件）

---

## 风险点 & 应对

| 风险 | 影响 | 应对 |
|------|------|------|
| 引擎事件结构变化 | Adapter 映射出错 | 用 `_SSE_RESULT_PREVIEW_CHARS` 等常量确认，写充分的单元测试 |
| LLM 标题生成延迟 | done 事件延迟发出 | 设置 3s 超时，超时用规则模板兜底 |
| 聚合窗口误判 | 不相关的工具被聚合 | 只聚合 tool_name 完全相同的连续调用 |
| 前端流式渲染性能 | 大量 thinking 文字卡顿 | 用 `requestAnimationFrame` 节流渲染，Markdown 渲染去抖 |
| Session 标题与 chat.py 冲突 | 同一会话两个端点写入 | SeeCrab 用 `channel="seecrab"` 隔离 session |
