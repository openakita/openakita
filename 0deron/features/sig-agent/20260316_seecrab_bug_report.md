# SeeCrab WebApp Bug Report

**测试日期**: 2026-03-16
**测试人**: QA (高级测试工程师)
**参考基准**: `0deron/features/sig-agent/20260341610_simple-agent-ui-demo.html`
**测试对象**: `apps/seecrab/` Vue 3 前端应用 (Vite dev server @ localhost:5174)
**测试方法**: Playwright 可视化对比测试 + 源码审查

---

## 概览

对比参考 demo HTML 与实际 SeeCrab webapp，发现 **10 个 Bug**，分为 3 个类别：
- **P0 (严重)**: 2 个 — 核心功能缺失/错误
- **P1 (重要)**: 4 个 — 布局/UI 与设计稿严重偏差
- **P2 (改进)**: 4 个 — 细节不一致

---

## P0 — 严重 (核心功能)

### BUG-001: 会话标题不生成，不沉淀到左侧会话列表

**现象**: 发起对话后，左侧会话列表始终显示 session ID 或 "新对话"，不会根据对话内容自动生成语义化标题。

**期望 (参考 demo)**: 发送消息后，会话标题自动更新为对话主题，如 "搜索 Karpathy 最新观点"、"分析代码库架构"。左侧列表实时反映更新后的标题。

**根因分析**:
1. **后端 `POST /api/seecrab/sessions`** (seecrab.py:261-265): 仅生成 `session_id`，不关联任何 session 对象到 `session_manager`
2. **后端 `POST /api/seecrab/chat`** (seecrab.py:107-127): 通过 `session_manager.get_session(create_if_missing=True)` 创建 session，但 session 对象没有 `title` 字段更新逻辑
3. **后端 `GET /api/seecrab/sessions`** (seecrab.py:214-224): 返回 `title: getattr(s, "title", s.id)`，降级为 session ID
4. **后端 `TitleGenerator` 存在但未连接**: `title_generator.py` 有完整的 LLM 标题生成逻辑，`SeeCrabAdapter` 也引用了它，但仅用于 step card 标题，**从未用于会话标题生成**
5. **前端 `LeftSidebar.vue`**: `{{ s.title || '新对话' }}` 逻辑正确，但后端不推送标题更新
6. **前端 `session.ts`**: `createSession()` 仅存储 `session_id`，不追加到 `sessions[]` 列表；`loadSessions()` 仅在初始化调用（如果调用的话），对话后不刷新

**影响范围**: `seecrab.py`, `session.ts`, `LeftSidebar.vue`

**修复方向**:
- 后端: 在首条消息发送后，用 TitleGenerator 生成会话标题并更新 session 对象
- 后端: 通过 SSE 事件（如 `session_title` 类型）推送标题到前端
- 前端: `createSession()` 后将新 session 追加到 `sessions[]`
- 前端: 接收到 `session_title` 事件后更新对应 session 的 title

---

### BUG-002: 计划 Checklist Checkbox 更新不在计划上，而在步骤卡片上

**现象**: Plan 模式下，计划步骤状态变化（pending → running → completed）仅体现在下方的 StepCard 卡片上，PlanChecklist 组件的 checkbox 不更新或不显示。

**期望 (参考 demo)**: "执行计划" 卡片中的每个步骤 checkbox 实时从灰色 (pending) → 蓝色动画 (running) → 绿色勾选 (completed) 变化，与下方 StepCard 同步。

**根因分析**:
1. **后端逻辑正确**: `step_aggregator.py` 的 `on_plan_step_updated()` 同时发送 `step_card` 和 `plan_checklist` 两种事件
2. **潜在问题1 — stepId 解析失败**: `seecrab_adapter.py:96-112` 中，engine 发送的 `stepId` 格式如果不匹配（不是 "step_N" 格式），则 `step_index` 为 0，直接 `return []`，导致 `plan_checklist` 事件被丢弃
3. **潜在问题2 — plan_created 未触发**: 如果 engine 不发送 `plan_created` 事件，aggregator 的 state 不是 `PLAN_ABSORB`，后续 `plan_step_updated` 全部被跳过并打 warning 日志
4. **前端渲染缺陷**: `PlanChecklist.vue` 缺少 "执行计划" 标题头和步骤编号，视觉上不像参考 demo 中的 "执行计划" 卡片

**影响范围**: `seecrab_adapter.py`, `step_aggregator.py`, `PlanChecklist.vue`

**修复方向**:
- 增强 stepId 解析容错性
- 确认 engine plan_created 事件是否正确发送
- 前端加 console.log 调试 plan_checklist 事件接收情况

---

## P1 — 重要 (布局/UI 偏差)

### BUG-004: 左侧边栏缺少品牌头部区域

**现象**: 侧边栏顶部直接是 "+ 新对话" 按钮，没有品牌标识。

**期望 (参考 demo)**: 侧边栏顶部有 56px 高的品牌区域，显示 agent logo icon (smart_toy) + "SeeAgent" 品牌名，与下方按钮以 border-bottom 分隔。

**影响文件**: `LeftSidebar.vue`

---

### BUG-005: 左侧边栏会话列表项缺少图标、步骤数、时间戳

**现象**: 会话列表项只有纯文本标题，无其他元数据。

**期望 (参考 demo)**: 每个会话项包含：
- 左侧: 类型图标 (chat/search/code/description)
- 标题文本
- 下方: "◇ N 步骤" 徽标 + 相对时间 "刚刚" / "3 小时前"

**影响文件**: `LeftSidebar.vue`, `session.ts` (需扩展 Session 类型)

---

### BUG-006: 左侧边栏缺少 "RECENT" 分区标签

**现象**: 会话列表直接开始，无分类标签。

**期望 (参考 demo)**: "+ New Chat" 按钮下方有灰色小字 "RECENT" 标签。

**影响文件**: `LeftSidebar.vue`

---

### BUG-009: 计划 Checklist 缺少 "执行计划" 标题头和步骤编号

**现象**: `PlanChecklist.vue` 直接渲染步骤列表，无卡片标题，无编号。

**期望 (参考 demo)**:
- 卡片顶部有 icon `checklist` + "执行计划" 标题
- 每个步骤前有 "1.", "2." 等编号
- checkbox 使用方形 check 图标（非 check_circle）

**影响文件**: `PlanChecklist.vue`

---

## P2 — 改进 (细节不一致)

### BUG-011: ReplyHeader 计时器格式不匹配

**现象**: 显示 "TTFT: 0.21s" 和分离的 total timer，中间无分隔符。

**期望 (参考 demo)**: 显示 "TTFT **1.05s** | Total **11.2s**"，两个 timer 之间用 "|" 分隔，数值加粗或高亮。

**影响文件**: `ReplyHeader.vue`

---

### BUG-012: UserMessage 气泡缺少头像

**现象**: 用户消息仅有蓝色气泡，右对齐。

**期望 (参考 demo)**: 气泡右侧有圆形用户头像 "U"。

**影响文件**: `UserMessage.vue`

---

### BUG-014: 右侧详情面板 header 缺少步骤标题和图标

**现象**: `RightPanel.vue` header 固定显示 "步骤详情" 文本。

**期望 (参考 demo)**: header 显示当前步骤的 emoji + 标题（如 "🔍 搜索 Karpathy 2026 最新动态"），与点击的 StepCard 标题一致。

**影响文件**: `RightPanel.vue`, `StepDetail.vue`

---

### BUG-015: 输入框底部栏宽度受限

**现象**: 输入框 max-width 限制为 `--chat-max-width: 720px`，在宽屏下显得太窄。

**期望 (参考 demo)**: 输入框接近 chat area 全宽，仅有左右 padding。

**影响文件**: `ChatInput.vue` (`.chat-input-container` max-width 限制)

---

## 附录: 截图对比

### 初始状态

| 参考 demo | 实际 SeeCrab |
|-----------|-------------|
| ![ref](reference-ui-initial.png) | ![actual](actual-ui-initial.png) |

**关键差异**: 品牌头部、会话列表内容、输入框宽度

### Plan 模式完成状态

| 参考 demo |
|-----------|
| ![ref-plan](reference-ui-plan-checklist-top.png) |

**关键差异**: "执行计划" 卡片标题、步骤编号、checkbox 样式

### 详情面板

| 参考 demo |
|-----------|
| ![ref-detail](reference-ui-detail-panel.png) |

**关键差异**: 面板 header 显示步骤标题而非固定 "步骤详情"

---

## 测试结论

当前 SeeCrab webapp 的核心交互框架（SSE 流式通信、消息/步骤渲染、详情面板）基本可用，但 **UI 布局与设计稿偏差较大**，集中在：

1. **左侧边栏几乎为空白** — 缺少品牌、会话元数据、分区标签
2. **会话标题完全不工作** — 后端→前端链路断裂
3. **Plan Checklist 不像设计稿** — 缺少标题头、编号、可能不更新

建议优先修复 P0 (BUG-001 ~ BUG-002)，然后批量处理 P1 布局问题。
