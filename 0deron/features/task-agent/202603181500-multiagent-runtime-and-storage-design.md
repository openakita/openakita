# 多 Agent 运行机制与数据存储设计

> 关联文档:
> - 需求文档: `202603171459-task-bestpractice-requirement-structured.md`
> - 技术设计: `202603181500-task-bestpractice-technical-design.md`
> 设计日期: 2026-03-18

---

## 1. 概念层级

引入最佳实践后，系统形成 **5 层概念层级**，每层有对应的代码实体和存储位置：

```
┌─────────────────────────────────────────────────────────────────────┐
│ 第1层：会话 (Session)                                                │
│ 代码实体：Session + SessionContext                                    │
│ 存储：SessionManager → data/sessions/sessions.json (5s 防抖写入)      │
│ 生命周期：用户发起对话 → 超时关闭（默认30min无活动）                     │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ 第2层：最佳实践实例 (BestPractice Instance)                    │    │
│  │ 代码实体：BPInstanceSnapshot                                   │    │
│  │ 存储：BPStateManager (内存，会话级生命周期)                     │    │
│  │ 生命周期：触发创建 → active/suspended 切换 → completed          │    │
│  │                                                                │    │
│  │  ┌─────────────────────────────────────────────────────────┐  │    │
│  │  │ 第3层：子任务 (SubTask)                                   │  │    │
│  │  │ 代码实体：SubtaskConfig + AgentProfile + DelegationRequest │  │    │
│  │  │ 存储：执行期 → SubAgent 临时上下文                         │  │    │
│  │  │       完成后 → BPInstanceSnapshot.subtask_outputs          │  │    │
│  │  │       + session.context.sub_agent_records                  │  │    │
│  │  │ 生命周期：BPEngine 调度 → SubAgent 执行 → 返回结果          │  │    │
│  │  │                                                            │  │    │
│  │  │  ┌──────────────────────────────────────────────────────┐ │  │    │
│  │  │  │ 第4层：步骤 (Step)                                    │ │  │    │
│  │  │  │ 代码实体：tool_use / tool_result (ReAct 循环内)        │ │  │    │
│  │  │  │ 存储：Brain.Context.messages (LLM 工作上下文)          │ │  │    │
│  │  │  │ 生命周期：单次工具调用 → 结果返回                      │ │  │    │
│  │  │  │                                                       │ │  │    │
│  │  │  │  ┌───────────────────────────────────────────────┐   │ │  │    │
│  │  │  │  │ 第5层：推理轮次 (Iteration)                     │   │ │  │    │
│  │  │  │  │ 代码实体：TaskState.iteration + Decision         │   │ │  │    │
│  │  │  │  │ 存储：ReasoningEngine 内部状态                   │   │ │  │    │
│  │  │  │  │ 生命周期：LLM 调用 → 解析决策 → 执行/终止        │   │ │  │    │
│  │  │  │  └───────────────────────────────────────────────┘   │ │  │    │
│  │  │  └──────────────────────────────────────────────────────┘ │  │    │
│  │  └─────────────────────────────────────────────────────────┘  │    │
│  └──────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### 概念映射表

| 产品概念 | 代码实体 | 存储位置 | 生命周期 | 数量关系 |
|---------|---------|---------|---------|---------|
| 会话 | `Session` + `SessionContext` | `sessions.json` (持久) | 30min 超时 | 1 个用户 : N 个会话 |
| BP 实例 | `BPInstanceSnapshot` | `BPStateManager` (内存) | 会话级 | 1 个会话 : N 个 BP 实例 |
| 子任务 | `SubtaskConfig` + `DelegationRequest` | 执行中临时 / 完成后快照 | 委派级 | 1 个 BP : N 个子任务 |
| 步骤 | `tool_use` + `tool_result` 消息对 | `Brain.Context.messages` | ReAct 循环内 | 1 个子任务 : N 个步骤 |
| 推理轮次 | `Decision` + `TaskState.iteration` | `ReasoningEngine` 内部 | 单次 LLM 调用 | 1 个步骤 : 1~N 轮推理 |
| 任务执行 | `TaskState` | `AgentState._tasks` (内存) | 单次请求-响应 | 1 个会话 : 串行 1 个 |
| Agent 角色 | `AgentProfile` | YAML 配置文件 | 静态配置 | N 个子任务共享 |
| Agent 实例 | `Agent` (缓存) | `AgentInstancePool` (内存) | 30min 空闲回收 | 1 个 Profile : 按会话缓存 |

---

## 2. 多 Agent 运行机制

### 2.1 Agent 类型

```
┌──────────────────────────────────────────────────────────────────┐
│                        Agent 类型体系                             │
│                                                                   │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│  │ MasterAgent  │    │  SubAgent   │    │ QueueAgent  │          │
│  │ (主 Agent)   │    │ (子 Agent)  │    │ (队列Agent) │          │
│  ├─────────────┤    ├─────────────┤    ├─────────────┤          │
│  │ 唯一实例     │    │ 按需创建    │    │ 后台执行    │          │
│  │ 完整上下文   │    │ 隔离上下文  │    │ 异步队列    │          │
│  │ 所有工具     │    │ 过滤工具    │    │ 优先级调度  │          │
│  │ 对话管理     │    │ 专项执行    │    │ 批量处理    │          │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘          │
│         │                  │                   │                  │
│         │   delegate()     │                   │                  │
│         ├─────────────────▶│                   │                  │
│         │   result         │                   │                  │
│         │◀─────────────────┤                   │                  │
│         │                                      │                  │
│         │          enqueue()                    │                  │
│         ├─────────────────────────────────────▶│                  │
│         │          wait_for() / fire-and-forget │                  │
│         │◀─────────────────────────────────────┤                  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Agent 实例生命周期

```
                    AgentFactory                AgentInstancePool
                        │                            │
  需要 SubAgent ─────▶ get_or_create()              │
                        │                            │
                        ├─ 缓存命中？ ───────────────▶ _pool[session::profile]
                        │   ├─ 命中 → 返回缓存实例     ← 更新 last_used
                        │   └─ 未命中 ↓                │
                        │                              │
                        ├─ create()                    │
                        │   ├─ 加载 AgentProfile       │
                        │   ├─ 实例化 Agent            │
                        │   ├─ _apply_skill_filter()   │
                        │   ├─ 配置 custom_prompt      │
                        │   └─ 存入缓存 ──────────────▶ _pool[session::profile] = entry
                        │                              │
                        ├─ 后台 Reaper (60s 轮询)       │
                        │   └─ idle > 30min? ─────────▶ 删除 entry, 清理 profile
                        │                              │
                        └─ skills_version 变更？ ──────▶ 失效全部缓存，重建
```

### 2.3 委派执行流程

```
MasterAgent                   AgentOrchestrator              SubAgent
    │                              │                            │
    │─ tool_use: delegate ────────▶│                            │
    │                              │                            │
    │                              │─ 1. 验证 depth < 5        │
    │                              │                            │
    │                              │─ 2. 记录 delegation_chain  │
    │                              │   session.context          │
    │                              │   .delegation_chain        │
    │                              │   .append({from, to, depth})│
    │                              │                            │
    │                              │─ 3. AgentFactory           │
    │                              │   .get_or_create(profile)  │
    │                              │        │                   │
    │                              │        └──────────────────▶│ (创建/复用)
    │                              │                            │
    │                              │─ 4. 注册 _sub_agent_states │
    │                              │   [session:profile] = {    │
    │                              │     status: "starting",    │
    │                              │     iteration: 0, ...      │
    │                              │   }                        │
    │                              │                            │
    │                              │─ 5. _call_agent() ────────▶│
    │                              │                            │── chat_with_session(
    │                              │                            │     message=委派消息,
    │                              │                            │     session_messages=[],
    │                              │                            │     session=共享Session,
    │                              │                            │   )
    │                              │                            │
    │                              │   6. 进度监控 (3s轮询)      │── ReAct Loop
    │                              │   _sub_agent_states 更新    │   ├─ Reason (LLM)
    │                              │   {status, iteration,      │   ├─ Act (Tools)
    │                              │    tools_executed, ...}     │   └─ Observe
    │                              │                            │
    │                              │                            │── SSE 事件 via
    │                              │                            │   session._sse_event_bus
    │                              │                            │   (thinking, tool_call,
    │                              │                            │    text_delta, ...)
    │                              │                            │
    │                              │◀── result_text ────────────│
    │                              │                            │
    │                              │─ 7. _persist_sub_agent_record()
    │                              │   session.context           │
    │                              │   .sub_agent_records        │
    │                              │   .append({                 │
    │                              │     agent_id, task_message,│
    │                              │     result_full,            │
    │                              │     tools_used, iterations,│
    │                              │     elapsed_s, ...          │
    │                              │   })                        │
    │                              │                            │
    │                              │─ 8. 记录 delegation_log    │
    │                              │   (JSONL 追加写入)          │
    │                              │                            │
    │                              │─ 9. 更新 _health 指标      │
    │                              │                            │
    │◀── tool_result: result ──────│                            │
    │                              │                            │
```

### 2.4 引入 BP 后的执行模型

> **组件关系**：`BPToolHandler` 与现有 `AgentToolHandler` 并列注册在 `SystemHandlerRegistry` 中，互不依赖。两者均通过 `agent._current_session` 获取 session，通过 `seeagent.main._orchestrator` 获取 orchestrator。`BPEngine` 通过 `AgentOrchestrator.delegate()` 复用现有的 SubAgent 执行路径。详见技术设计文档 §1.3 和 §10.2。

```
用户消息
  │
  ▼
MasterAgent ReAct Loop
  │
  ├─ [普通对话] 无 BP 匹配
  │   └─ 正常推理 → 工具调用 → 返回
  │
  ├─ [BP 启动] 匹配触发条件
  │   └─ tool_use: bp_start(bp_id, input_data)
  │       │
  │       ▼
  │   BPToolHandler → BPEngine
  │       │
  │       ├─ 创建 BPInstanceSnapshot
  │       │
  │       ├─ 子任务 1: delegate() → SubAgent-1
  │       │   SubAgent-1 ReAct Loop:
  │       │     Reason → Act(web_search) → Observe
  │       │     Reason → Act(read_file) → Observe
  │       │     Reason → Final Answer (含 output JSON)
  │       │   ← result
  │       │
  │       ├─ 存储 output → BPInstanceSnapshot.subtask_outputs
  │       │
  │       ├─ [auto 模式] → 子任务 2: delegate() → SubAgent-2
  │       │   ...递归直到全部完成
  │       │
  │       └─ [manual 模式] → 返回完成信息
  │           MasterAgent → ask_user("查看结果 / 进入下一步")
  │           ← 等待用户
  │
  ├─ [BP 继续] 用户点击"进入下一步"
  │   └─ tool_use: bp_continue(instance_id)
  │       └─ 子任务 N: delegate() → SubAgent-N → ...
  │
  ├─ [Chat-to-Edit] 用户修改子任务输出
  │   └─ tool_use: bp_edit_output(instance_id, subtask_id, changes)
  │       └─ deep merge → mark stale → 返回 diff
  │
  └─ [任务切换] 用户切换到另一个 BP
      └─ tool_use: bp_switch_task(target_instance_id)
          ├─ 挂起当前：compress context → contextSummary
          ├─ 恢复目标：load snapshot → inject context
          └─ 返回恢复信息
```

### 2.5 并发与互斥

| 维度 | 机制 | 说明 |
|------|------|------|
| 同一会话并发请求 | `busy_lock`（SeeCrab API） | 同一 conversation_id 的请求串行化 |
| 同一 Agent 并发委派 | `_create_locks[key]` | 同一 session::profile 的创建串行化 |
| 跨会话并发 | 无互斥 | 不同会话完全独立，可并行 |
| BP 实例互斥 | `BPStateManager._active_map` | 同一会话同时只有一个 active BP |
| 用户消息注入 | `TaskState._insert_lock` | 异步锁保护 `pending_user_inserts` 队列 |
| Session 持久化 | `SessionManager._save_lock` | 防抖 5s + 原子写入（temp → rename） |
| 内存存储 | `BPStateManager._lock` (asyncio.Lock) | 保护 `_instances` 和 `_active_map`（async 上下文，不可使用 threading.RLock）|

---

## 3. 数据存储全景

### 3.1 存储层级

```
┌──────────────────────────────────────────────────────────────────────┐
│  持久层 (进程重启后存活)                                               │
│                                                                       │
│  ┌─ SQLite (data/memory/seeagent.db) ────────────────────────────┐   │
│  │  memories        — 语义记忆（事实/偏好/规则/技能/经验）         │   │
│  │  episodes        — 交互片段（目标/结果/动作链/实体）            │   │
│  │  conversation_turns — 对话轮次（按 session+turn_index）        │   │
│  │  extraction_queue — 待提取队列（重试机制）                      │   │
│  │  scratchpad      — 跨会话工作记忆（笔记/项目/下一步）          │   │
│  │  attachments     — 文件/媒体记忆（描述/转录/OCR）              │   │
│  │  embedding_cache — 向量缓存（content_hash → embedding）        │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─ JSON 文件 ──────────────────────────────────────────────────┐    │
│  │  data/sessions/sessions.json      — 会话状态 (防抖5s写入)    │    │
│  │  data/sessions/channel_registry.json — 频道目标映射           │    │
│  │  data/users/users.json            — 用户画像 + 跨平台绑定    │    │
│  │  data/delegation_logs/{YYYYMMDD}.jsonl — 委派日志 (追加写入) │    │
│  │  data/delegation_logs/sub_agent_states.json — 子Agent终态    │    │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─ YAML 配置文件 ──────────────────────────────────────────────┐    │
│  │  bestpractice/configs/*.yaml      — BP 模板定义 (系统内置)   │    │
│  │  {project}/bestpractice/*.yaml    — BP 模板定义 (用户自定义) │    │
│  │  agents/profiles/*.yaml           — Agent 角色定义           │    │
│  │  skills/*/SKILL.md                — 技能定义                 │    │
│  └───────────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────────┤
│  会话级 (会话存活期间)                                                │
│                                                                       │
│  ┌─ SessionContext (序列化到 sessions.json) ──────────────────┐     │
│  │  messages: list[dict]          — 统一消息时间线             │     │
│  │  delegation_chain: list[dict]  — 委派深度链                 │     │
│  │  sub_agent_records: list[dict] — 子 Agent 工作记录 (≤50条) │     │
│  │  handoff_events: list[dict]    — SSE 事件持久化             │     │
│  │  agent_switch_history          — Agent 切换历史             │     │
│  │  topic_boundaries: list[int]   — 话题边界索引               │     │
│  │  variables: dict               — 会话变量                   │     │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─ BPStateManager (内存，不序列化) ─────────────────────────┐      │
│  │  _instances: { instance_id → BPInstanceSnapshot }         │      │
│  │  _active_map: { session_id → active_instance_id }         │      │
│  │  _session_index: { session_id → [instance_ids] }          │      │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─ AgentInstancePool (内存) ────────────────────────────────┐      │
│  │  _pool: { "session::profile" → _PoolEntry(agent, times) } │      │
│  │  空闲 30min 回收                                            │      │
│  └───────────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────────┤
│  任务级 (单次请求-响应周期)                                           │
│                                                                       │
│  ┌─ TaskState (AgentState._tasks) ───────────────────────────┐      │
│  │  task_id, session_id, status, iteration                    │      │
│  │  cancel_event, skip_event, pending_user_inserts            │      │
│  │  tools_executed, recent_tool_signatures (循环检测)          │      │
│  │  original_user_messages (模型切换备份)                      │      │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─ Brain.Context (LLM 工作窗口) ────────────────────────────┐      │
│  │  messages: list[MessageParam]  — 当前 LLM 输入消息列表      │      │
│  │  system: str                   — 组装后的系统提示            │      │
│  │  tools: list[ToolParam]        — 当前可用工具定义            │      │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─ ReasoningEngine 内部状态 ─────────────────────────────────┐     │
│  │  working_messages: list[dict]  — 可变消息列表（压缩目标）    │     │
│  │  checkpoints: list[Checkpoint] — 决策检查点（支持回滚）      │     │
│  │  _last_react_trace: list       — 最近执行轨迹               │     │
│  └───────────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────────┤
│  临时级 (秒级存活)                                                    │
│                                                                       │
│  ┌─ SubAgent Brain.Context ──────────────────────────────────┐      │
│  │  创建：delegate() 调用时                                    │      │
│  │  销毁：子任务完成，结果以 tool_result 返回 MasterAgent       │      │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─ SSE EventBus (asyncio.Queue) ────────────────────────────┐      │
│  │  session.context._sse_event_bus                            │      │
│  │  消费即销毁，不持久化                                       │      │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─ Tool 执行结果 ───────────────────────────────────────────┐      │
│  │  tool_result 注入 working_messages 后即完成使命             │      │
│  │  大型结果 (>5000 token) 被 ContextManager 压缩              │      │
│  └───────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.2 实体关系图

```
User 1────N Session
               │
               ├──1 SessionContext
               │      ├── messages[]  ← 统一时间线
               │      │     每条消息可选 metadata:
               │      │     { bp_instance_id, subtask_id, bp_event }
               │      │
               │      ├── sub_agent_records[]  ← 子 Agent 工作记录
               │      │     { agent_id, task_message, result_full,
               │      │       tools_used[], iterations, elapsed_s }
               │      │
               │      └── delegation_chain[]  ← 委派深度链
               │            { from, to, depth, timestamp }
               │
               ├──N BPInstanceSnapshot  (via BPStateManager)
               │      │
               │      ├──1 BestPracticeConfig  (配置引用)
               │      │      │
               │      │      └──N SubtaskConfig
               │      │            │
               │      │            └──1 AgentProfile (via agent_profile id)
               │      │                  │
               │      │                  └──1 Agent Instance (via Pool)
               │      │
               │      ├── subtask_outputs: { subtask_id → JSON }
               │      ├── subtask_statuses: { subtask_id → status }
               │      └── context_summary: string (挂起时生成)
               │
               └──1 TaskState  (via AgentState, 当前活跃任务)
                      │
                      ├── pending_user_inserts[]
                      ├── cancel_event / skip_event
                      └── iteration / tools_executed[]

AgentOrchestrator (全局单例)
  ├── _sub_agent_states: { "session:profile" → 进度状态 }
  ├── _health: { agent_id → AgentHealth }
  ├── _mailboxes: { agent_id → AgentMailbox }
  └── _active_tasks: { session_key → [asyncio.Task] }

MemoryManager (全局单例)
  └── UnifiedStore (SQLite)
        ├── memories (语义记忆)
        ├── episodes (交互片段)
        ├── conversation_turns (对话轮次)
        ├── extraction_queue (异步提取)
        ├── scratchpad (工作记忆)
        ├── attachments (文件/媒体)
        └── embedding_cache (向量)
```

### 3.3 BPInstanceSnapshot 详细字段

```python
@dataclass
class BPInstanceSnapshot:
    """完整的 BP 实例状态，是 BP 数据存储的核心实体"""

    # ── 身份标识 ──
    bp_id: str                      # BestPracticeConfig.id（模板 ID）
    instance_id: str                # 运行时唯一 ID (uuid4)
    session_id: str                 # 所属会话 ID

    # ── 生命周期 ──
    status: BPStatus                # active / suspended / completed
    created_at: float               # time.time()
    suspended_at: float | None      # 最近一次挂起时间
    completed_at: float | None      # 完成时间

    # ── 执行进度 ──
    current_subtask_index: int      # 当前执行到第几步（0-based）
                                    # 线性模式下的便捷指针，DAG 模式下由 subtask_statuses 推导
    run_mode: RunMode               # manual / auto
    subtask_statuses: dict          # { subtask_id → "pending"|"current"|"done"|"stale" }
                                    # 执行进度的**主真值源**

    # ── 数据 ──
    subtask_outputs: dict           # { subtask_id → output JSON dict }
                                    # 特殊 key "__initial_input__" 存储初始输入
    # ── 上下文 ──
    context_summary: str            # 挂起时 LLM 压缩的上下文摘要
                                    # 恢复时作为 [上下文恢复] 注入
    master_messages_snapshot: list[dict] | None  # 挂起时 MasterAgent.Context.messages 副本
                                    # 可选，用于精确恢复（与技术设计 §3.2 对齐）

    # ── 配置引用 ──
    bp_config: BestPracticeConfig   # 关联的模板配置（运行时引用）
```

### 3.4 数据生命周期对照

| 数据 | 创建时机 | 更新时机 | 销毁/归档时机 |
|------|---------|---------|-------------|
| **Session** | 用户首次对话 | 每次活动 touch() | 超时 30min 后标记 EXPIRED |
| **SessionContext.messages** | 随 Session 创建 | 每条消息追加 | 超过 max_history 时截断 + 摘要 |
| **BPInstanceSnapshot** | `bp_start` 工具调用时 | 每个子任务完成 / 编辑 / 挂起 / 恢复 | 会话结束时随 BPStateManager 释放 |
| **subtask_outputs** | 子任务首次完成时写入 | Chat-to-Edit 修改时更新 | 随 BPInstanceSnapshot 释放 |
| **context_summary** | 任务挂起时生成 | 每次挂起重新生成 | 恢复后消费（注入上下文），但保留在快照中 |
| **sub_agent_records** | SubAgent 返回结果时 | 不更新（追加写入） | 会话序列化时持久化，上限 50 条 |
| **TaskState** | `begin_task()` 创建 | ReAct 循环每轮更新 | `reset_task()` 销毁 |
| **Brain.Context** | Agent 初始化时 | 每轮推理更新 messages | Agent 释放时随之销毁 |
| **delegation_log** | 每次委派事件 | 追加写入 JSONL | 30 天轮转删除 |
| **memories (SQLite)** | 提取器异步生成 | access_count / decay 更新 | TTL 过期清理 |
| **episodes (SQLite)** | 会话结束 / 压缩触发 | 不更新 | 不自动删除 |

---

## 4. 上下文数据流

### 4.1 正常对话（无 BP）

```
用户消息 "你好"
    │
    ▼
[SessionContext.messages] ← add_message("user", "你好", timestamp=...)
    │
    ▼
[MasterAgent._prepare_session_context()]
    ├─ session_messages = session.context.get_messages()
    ├─ 话题检测 → 可能插入 [上下文边界]
    ├─ ContextManager.compress_if_needed(messages)
    └─ 构建 working_messages
    │
    ▼
[Brain.Context.messages] = working_messages
    │
    ▼
[ReasoningEngine.reason_stream()]
    ├─ LLM 调用 → Decision
    ├─ 工具执行 → tool_result 追加到 messages
    └─ 最终回答
    │
    ▼
[SessionContext.messages] ← add_message("assistant", response, timestamp=...)
[MemoryManager] ← record_turn("assistant", response, tool_calls=...)
```

### 4.2 BP 执行流（手动模式，2 个子任务）

```
时间 │  SessionContext.messages        Brain.Context            BPStateManager
     │  (统一时间线，持久)              (LLM工作窗口，运行时)     (BP 状态，内存)
     │
 T1  │  ← [user] "做市场调研"         ← [user] "做市场调研"     (空)
     │
 T2  │  ← [assistant] ask_user        ← [assistant] ask_user
     │    选项：自由/最佳实践            选项：自由/最佳实践
     │
 T3  │  ← [user] "最佳实践模式"       ← [user] tool_result
     │    (bp_instance_id=null)
     │
 T4  │                                ← [assistant] tool_use:    instance "bp-001" 创建
     │                                   bp_start(id, input)     status=ACTIVE
     │                                                            current_index=0
     │  ← [assistant] "开始执行..."    ┌────────────────────────┐
     │    (bp_instance_id="bp-001")   │ SubAgent-1 (临时上下文)  │
     │                                │ [user] 委派消息          │
     │  (SubAgent 的 SSE 事件          │ [asst] tool_use:search  │
     │   通过 event_bus 流出，         │ [user] tool_result      │
     │   但不写入统一时间线)            │ [asst] Final: {...}     │
     │                                └────────────────────────┘
     │                                                            subtask_outputs
 T5  │                                ← [user] tool_result:       ["research"] = {output}
     │                                   {subtask_1 完成, JSON}   subtask_statuses
     │                                                            ["research"] = "done"
     │                                                            current_index = 1
     │
 T6  │  ← [assistant] "子任务1完成"    ← [assistant] ask_user
     │    (bp_instance_id="bp-001")     "查看结果 / 进入下一步"
     │
     │  ══════ 暂停点：用户可自由对话 ══════
     │
 T7  │  ← [user] "进入下一步"         ← [user] tool_result
     │    (bp_instance_id="bp-001")
     │
 T8  │                                ← [assistant] tool_use:
     │                                   bp_continue("bp-001")
     │  ← [assistant] "执行子任务2..."  ┌────────────────────────┐
     │    (bp_instance_id="bp-001")   │ SubAgent-2 (临时上下文)  │
     │                                │ 输入 = subtask_outputs   │
     │                                │         ["research"]     │
     │                                └────────────────────────┘
     │                                                            subtask_outputs
 T9  │                                ← [user] tool_result:       ["analysis"] = {output}
     │                                   {subtask_2 完成, JSON}   status = COMPLETED
     │
 T10 │  ← [assistant] "全部完成"       ← [assistant] 最终回答
     │    (bp_instance_id="bp-001")
     ▼
```

### 4.3 任务切换时的数据流

```
时间 │  Brain.Context              BPStateManager                      ContextManager
     │  (MasterAgent 工作窗口)     (状态快照)                          (压缩引擎)
     │
     │  内容：BP-001 的对话历史    BP-001: active, index=2
     │  [user] 做市场调研          BP-002: suspended, index=0
     │  [asst] bp_start result
     │  [user] 进入下一步
     │  [asst] bp_continue result
     │  ... (可能很长)
     │
     │  ← [user] "回到竞品分析"    LLM 意图路由 → 切换到 BP-002
     │
     │ ┌─ 步骤 1: 挂起 BP-001 ─────────────────────────────────────┐
     │ │                                                            │
     │ │ Brain.Context.messages ─────────────────────────────────▶ compress_if_needed()
     │ │                           │                                │
     │ │                           │                     返回 contextSummary 文本
     │ │                           ▼                                │
     │ │                   BP-001: suspended              │
     │ │                   context_summary = "用户要求..."  │
     │ │                   subtask_outputs 保持不变         │
     │ └───────────────────────────────────────────────────────────┘
     │
     │ ┌─ 步骤 2: 清空工作窗口 ────────────────────────────────────┐
     │ │                                                            │
     │ │ Brain.Context.messages = []                                │
     │ │                                                            │
     │ └───────────────────────────────────────────────────────────┘
     │
     │ ┌─ 步骤 3: 恢复 BP-002 ─────────────────────────────────────┐
     │ │                                                            │
     │ │ BP-002: active                                             │
     │ │ snapshot = BPStateManager.resume("bp-002")                 │
     │ │                                                            │
     │ │ ContextBridge.prepare_restore_messages(snapshot)            │
     │ │ ▼                                                          │
     │ │ Brain.Context.messages = [                                 │
     │ │   {role: "user", content: "[上下文恢复] 竞品分析任务...     │
     │ │    子任务1 已完成，输出: {...}                               │
     │ │    子任务2~4 待执行                                         │
     │ │    上下文摘要: 用户要求分析5家竞品..."},                     │
     │ │ ]                                                          │
     │ └───────────────────────────────────────────────────────────┘
     │
     │ ┌─ 步骤 4: 更新系统提示 ────────────────────────────────────┐
     │ │                                                            │
     │ │ system_prompt 中 BP_DYNAMIC 段更新为：                      │
     │ │ "当前最佳实践任务：                                         │
     │ │   ● [活跃] 竞品技术分析 — 1/4 已完成                       │
     │ │   ○ [挂起] 市场调研报告 — 2/2 已完成，挂起于刚才"           │
     │ │                                                            │
     │ └───────────────────────────────────────────────────────────┘
     │
     │  继续 BP-002 的对话...
     ▼
```

---

## 5. 存储一致性保障

### 5.1 写入顺序

BP 操作涉及多个存储位置的更新，需要保证顺序：

```
子任务完成时的写入顺序：
  1. BPStateManager.update_subtask_output()    ← 内存，立即生效
  2. BPStateManager.advance_subtask()           ← 内存，立即生效
  3. emit bp_progress event → SSE EventBus     ← 异步，前端更新
  4. emit bp_subtask_output event → SSE EventBus
  5. _persist_sub_agent_record(session)          ← 会话级，5s 防抖写入
  6. session.context.add_message(bp_instance_id) ← 统一时间线追加

任务切换时的写入顺序：
  1. ContextBridge.compress_for_suspend()        ← 异步 LLM 调用
  2. BPStateManager.suspend(current_id, summary) ← 内存，立即
  3. BPStateManager.resume(target_id)            ← 内存，立即
  4. Brain.Context.messages = []                 ← 清空工作窗口
  5. Brain.Context.messages = restore_messages   ← 注入恢复内容
  6. emit bp_task_switch event                   ← 前端更新
```

### 5.2 故障恢复

| 故障场景 | 影响 | 恢复策略 |
|---------|------|---------|
| 子任务执行中进程崩溃 | BPInstanceSnapshot 在内存中丢失 | 当前无持久化，需用户重新启动 BP。**后续可扩展**：将 BPStateManager 序列化到 sessions.json |
| 子任务超时 | SubAgent 被 kill | AgentOrchestrator 返回 timeout 错误，BPEngine 可重试或通知用户 |
| 上下文压缩失败 | contextSummary 为空 | 使用 subtask_outputs 作为最小恢复信息，提示用户可能丢失部分上下文 |
| SSE 连接断开 | 前端错过 bp_* 事件 | 前端重连后发送 `bp_get_status` 请求全量状态同步 |

### 5.3 BP 持久化扩展方案（预留）

当前 `BPStateManager` 是纯内存存储。后续如需跨进程恢复，可扩展为：

```python
class BPStateManager:
    def _serialize_to_session(self, session: Session) -> None:
        """将所有实例快照序列化到 session.metadata['_bp_instances']"""
        session.set_metadata("_bp_instances", {
            iid: asdict(snap) for iid, snap in self._instances.items()
            if snap.session_id == session.id
        })

    def _restore_from_session(self, session: Session) -> None:
        """从 session.metadata 恢复实例状态"""
        saved = session.get_metadata("_bp_instances", {})
        for iid, data in saved.items():
            self._instances[iid] = BPInstanceSnapshot(**data)
```

这样 BP 状态随 Session 一起持久化到 `sessions.json`，无需新增存储文件。

---

## 6. 数据读写矩阵

各模块对各数据实体的读写权限：

| 数据实体 | BPEngine | BPToolHandler | MasterAgent | SubAgent | SeeCrabAdapter | Frontend |
|---------|:--------:|:------------:|:-----------:|:--------:|:-------------:|:--------:|
| **BPInstanceSnapshot** | R/W | R | — | — | — | — |
| **BPStateManager** | R/W | R/W | — | — | — | — |
| **SessionContext.messages** | — | W (标签) | R/W | R (共享引用) | R (序列化) | — |
| **sub_agent_records** | — | — | — | — | R (展示) | R |
| **Brain.Context (Master)** | — | W (切换时替换) | R/W | — | — | — |
| **Brain.Context (Sub)** | — | — | — | R/W (临时) | — | — |
| **TaskState** | R (检查取消) | — | R/W | R/W | — | — |
| **SSE EventBus** | W (bp_*) | — | — | W (标准事件) | R (消费) | R (接收) |
| **Prompt 模板** | R (渲染) | — | — | — | — | — |
| **BP 配置 (YAML)** | R | R | — | — | — | — |
| **AgentProfile** | R | — | — | R (自身) | — | — |
| **AgentInstancePool** | — | — | — | — | — | — |
| **delegation_log** | — | — | — | — | — | — |
| **MemoryManager** | — | — | R/W | R/W | — | — |

> R = 读取, W = 写入, R/W = 读写, — = 不直接访问

---

## 7. 容量与性能考量

| 指标 | 限制 | 机制 |
|------|------|------|
| 单会话 BP 实例数 | 无硬限制，建议 ≤10 | BPStateManager 内存占用 ~1KB/实例 |
| 单 BP 子任务数 | 无硬限制，建议 ≤10 | 每个子任务一次 delegate()，耗时取决于任务复杂度 |
| sub_agent_records | ≤50 条/会话 | 循环覆盖 |
| SessionContext.messages | ≤100 条 (max_history) | 超出后截断 + 摘要 |
| Brain.Context tokens | ≤160,000 | ContextManager 自动压缩 (soft=85%, hard=100%) |
| 委派深度 | ≤5 层 | `MAX_DELEGATION_DEPTH` |
| 并发子任务 | 串行（BP 当前按线性顺序执行） | 线性模式递归调用，无并行。DAG 扩展预留：ready 集合内子任务可 asyncio.gather 并行 |
| Agent 实例缓存 | 按需，空闲 30min 回收 | AgentInstancePool reaper |
| 上下文压缩比 | 15% (普通) / 18% (边界) | ContextManager 常量 |
| 大型 tool_result 压缩阈值 | 5000 tokens | 超出后 LLM 压缩 |
