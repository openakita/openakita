# 最佳实践(BP)手动模式 — 子任务流转与控制全流程

> 日期: 2026-03-19
> 基于代码版本: main 分支当前状态

---

## 1. 总览

手动模式下，一个 BP 总任务从触发到完成的完整生命周期采用 **Pull 模型**：每个子任务执行完后暂停，等待用户通过 UI 按钮主动推进下一步。

```
用户输入 → 触发识别 → bp_start → 子任务1执行 → 完成暂停
                                                    │
                                    用户点击[进入下一步]
                                                    │
                                  bp_continue → 子任务2执行 → 完成暂停
                                                                  │
                                                  用户点击[进入下一步]
                                                                  │
                                                bp_continue → 子任务3执行 → 全部完成
```

---

## 2. 完整流程 ASCII 图

```
用户输入 "帮我做市场调研报告"
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 0: 触发识别                                                │
│                                                                  │
│  prompt_assembler._build_bp_section()                            │
│    ├─ get_static_prompt_section()  → 注入可用 BP 列表 + 触发规则  │
│    └─ get_dynamic_prompt_section() → 注入当前状态 (首次为空)      │
│                                                                  │
│  LLM 识别关键词匹配 → 调用 ask_user 询问用户                     │
│    "检测到你可能需要「市场调研报告」，是否启用？"                   │
│    [自由模式]  [最佳实践模式]                                     │
│                                                                  │
│  用户点击 [最佳实践模式] → 发送用户消息                           │
│  LLM 调用 bp_start(bp_id="market-research", run_mode="manual")  │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 1: 创建实例 (handler._handle_start)                        │
│                                                                  │
│  state_manager.create_instance(bp_config, session_id)            │
│    ├─ instance_id = "bp-a1b2c3d4"                                │
│    ├─ status = ACTIVE                                            │
│    ├─ run_mode = MANUAL                                          │
│    ├─ current_subtask_index = 0                                  │
│    └─ subtask_statuses = {                                       │
│         "research": "pending",                                   │
│         "analysis": "pending",                                   │
│         "report":   "pending"                                    │
│       }                                                          │
│                                                                  │
│  → 进入 engine.execute_subtask()                                 │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 2: 执行子任务 1 (engine.execute_subtask)                   │
│                                                                  │
│  ① _resolve_input()                                              │
│     └─ idx=0 → 使用 snap.initial_input                           │
│                                                                  │
│  ② _check_input_completeness()                                   │
│     └─ 检查 input_schema.required 字段                           │
│     └─ 缺失 → 返回提示, LLM 调用 bp_supplement_input            │
│                                                                  │
│  ③ update_subtask_status("research", CURRENT)                    │
│                                                                  │
│  ④ _emit_progress() ─── SSE ──→ { type: "bp_progress",          │
│     │                              statuses: {research: current, │
│     │                                         analysis: pending, │
│     │                                         report: pending},  │
│     │                              current_subtask_index: 0 }    │
│     │                                                            │
│     └─ 前端: bpStore.handleBPProgress()                          │
│        └─ TaskProgressCard 渲染: ●research ○analysis ○report     │
│                                                                  │
│  ⑤ _build_delegation_message()                                   │
│     └─ 构建: BP名 + 子任务名 + 输入JSON + 输出Schema             │
│                                                                  │
│  ⑥ orchestrator.delegate(to_agent="research-agent", message=...) │
│     └─ SubAgent 独立执行 (session_messages=[] 上下文隔离)         │
│     └─ 期间产生 agent_header / step_card / ai_text 等 SSE 事件   │
│     └─ 返回 JSON 结果字符串                                      │
│                                                                  │
│  ⑦ _parse_output(result) → dict                                  │
│  ⑧ update_subtask_output("research", output)                     │
│  ⑨ update_subtask_status("research", DONE)                       │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 3: 子任务完成处理                                          │
│                                                                  │
│  ⑩ _emit_subtask_output() ── SSE ──→ { type: "bp_subtask_output"│
│     │                                   subtask_id: "research",  │
│     │                                   output: {...} }          │
│     └─ 前端: bpStore.handleSubtaskOutput()                       │
│        └─ inst.subtaskOutputs["research"] = output               │
│                                                                  │
│  ⑪ _persist() → session.metadata["bp_state"] = serialized       │
│                                                                  │
│  ⑫ is_last? → idx=0, total=3 → false                            │
│                                                                  │
│  ⑬ advance_subtask() → current_subtask_index: 0 → 1             │
│                                                                  │
│  ⑭ _emit_subtask_complete() ── SSE ──→ { type: "bp_subtask_     │
│     │                                     complete",             │
│     │                                     subtask_name: "市场调研"│
│     │                                     summary: "输出预览...",│
│     │                                     is_last: false }       │
│     │                                                            │
│     └─ 前端: reply.subtaskComplete = {...}                       │
│        └─ BotReply 渲染 SubtaskCompleteBlock:                    │
│           ┌──────────────────────────────────────┐               │
│           │ ✅ 子任务「市场调研」      已完成     │               │
│           │ 摘要: 输出预览...                     │               │
│           │  [查看结果]    [进入下一步]            │               │
│           └──────────────────────────────────────┘               │
│                                                                  │
│  ⑮ _emit_progress() ── SSE ──→ { statuses: {research: done,     │
│     │                                        analysis: pending}, │
│     │                             current_subtask_index: 1 }     │
│     └─ 前端: TaskProgressCard 更新: ✓research ○analysis ○report  │
│                                                                  │
│  ⑯ return _format_subtask_complete_result()                      │
│     └─ 返回给 LLM 的工具结果文本:                                │
│        "子任务「市场调研」已完成。输出预览: ...                    │
│         下一步是「数据分析」。前端已向用户展示操作按钮。           │
│         请不要调用 ask_user，等待用户操作即可。                    │
│         当用户发送「进入下一步」时，调用 bp_continue(...)"        │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 4: 暂停等待用户操作                                        │
│                                                                  │
│  LLM 收到工具结果 → 输出简短文本 → SSE done                      │
│                                                                  │
│  此时前端状态:                                                    │
│  ┌────────────────────────────────────────────────────┐          │
│  │ TaskProgressCard: ✓research ○analysis ○report      │          │
│  │                                                    │          │
│  │ [思考完成 ▸]                                       │          │
│  │ ✅ 委派 research-agent: 市场调研...        285s ▸  │          │
│  │   ✅ 搜索 "Token 商业模式"                 3.8s ▸  │          │
│  │   ✅ 浏览 白皮书                          12.4s ▸  │          │
│  │                                                    │          │
│  │ LLM 回复文本...                                    │          │
│  │                                                    │          │
│  │ ┌ ✅ 子任务「市场调研」      已完成 ─────────┐     │          │
│  │ │ 摘要: 输出预览...                          │     │          │
│  │ │  [查看结果]    [进入下一步]                 │     │          │
│  │ └────────────────────────────────────────────┘     │          │
│  └────────────────────────────────────────────────────┘          │
│                                                                  │
│  用户可选操作:                                                    │
│  A) 点击 [查看结果]                                              │
│     └─ 纯 UI 操作: uiStore.openSubtaskOutput(instanceId, stId)  │
│     └─ 右侧面板打开 SubtaskOutputPanel (可编辑输出)              │
│     └─ 不发送任何消息给后端                                      │
│                                                                  │
│  B) 点击 [进入下一步]                                            │
│     └─ 发送用户消息 "进入下一步"                                 │
│     └─ sseClient.sendMessage("进入下一步", convId)               │
└──────────────────────────────────────────────────────────────────┘
  │
  │ 用户点击 [进入下一步]
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 5: 推进到子任务 2                                          │
│                                                                  │
│  新一轮 seecrab_chat(message="进入下一步")                       │
│                                                                  │
│  prompt_assembler 注入动态 BP 段:                                │
│    status_table:                                                 │
│      | bp-a1b2c3d4 | 市场调研报告 | active | 1/3 | manual |     │
│    active_context:                                               │
│      "当前活跃任务: 市场调研报告 (进度: 1/3)"                     │
│    intent_routing:                                               │
│      "用户可能想要:                                               │
│       A) 修改上一步结果 (bp_edit_output)                          │
│       B) 继续下一步 — 用户说「进入下一步」时立即调用 bp_continue  │
│       C) 切换到其他任务 (bp_switch_task)                          │
│       D) 询问相关问题                                             │
│       E) 开始新话题"                                              │
│                                                                  │
│  LLM 识别 "进入下一步" → 调用 bp_continue(instance_id=...)       │
│                                                                  │
│  handler._handle_continue():                                     │
│    ├─ resolve_instance_id → "bp-a1b2c3d4"                        │
│    ├─ reset_stale_if_needed() (无 stale, 跳过)                   │
│    └─ engine.execute_subtask() ← current_subtask_index=1         │
│                                                                  │
│  → 重复 Phase 2-3 流程, 执行子任务 "analysis"                    │
│    ├─ _resolve_input(): 使用 subtask_outputs["research"] 作为输入│
│    ├─ delegate to analysis-agent                                 │
│    ├─ 完成后 emit bp_subtask_complete(is_last=false)             │
│    └─ advance_subtask() → current_subtask_index: 1 → 2          │
└──────────────────────────────────────────────────────────────────┘
  │
  │ 用户再次点击 [进入下一步]
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 6: 执行最后一个子任务 (子任务 3)                           │
│                                                                  │
│  同 Phase 5, LLM 调用 bp_continue                               │
│  engine.execute_subtask() ← current_subtask_index=2             │
│                                                                  │
│  执行 "report" 子任务...                                         │
│                                                                  │
│  完成后:                                                         │
│  ⑫ is_last? → idx=2, total=3 → true                             │
│     └─ state_manager.complete(instance_id)                       │
│        └─ snap.status = COMPLETED                                │
│                                                                  │
│  ⑬ (跳过 advance_subtask, 已是最后一个)                          │
│                                                                  │
│  ⑭ _emit_subtask_complete(is_last=true) ── SSE ──→              │
│     └─ 前端: SubtaskCompleteBlock 渲染:                          │
│        ┌──────────────────────────────────────┐                  │
│        │ ✅ 子任务「报告生成」      全部完成   │                  │
│        │ 摘要: 输出预览...                     │                  │
│        │  [查看最终报告]                       │                  │
│        └──────────────────────────────────────┘                  │
│                                                                  │
│  ⑮ _emit_progress() → statuses 全部 done, status=completed      │
│     └─ TaskProgressCard: ✓research ✓analysis ✓report             │
│                                                                  │
│  ⑯ return _format_completion_result()                            │
│     └─ "🎉 最佳实践「市场调研报告」全部完成！                     │
│         共完成 3 个子任务。请向用户展示最终结果摘要。"             │
│                                                                  │
│  LLM 输出最终摘要文本 → SSE done                                 │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. SSE 事件时序 (单个子任务)

```
时间 ──────────────────────────────────────────────────────────────→

后端 engine.execute_subtask()
  │
  ├─ ③ update_status(CURRENT)
  │
  ├─ ④ emit ─────→ [bp_progress]          前端: TaskProgressCard 更新
  │                  statuses.research=current
  │
  ├─ ⑥ delegate ──→ [agent_header]         前端: 切换 agent 上下文
  │                  [step_card running]    前端: StepCard 出现
  │                  [thinking]             前端: ThinkingBlock
  │                  [step_card completed]  前端: StepCard 完成
  │                  [ai_text]             前端: agent 回复文本
  │
  ├─ ⑩ emit ─────→ [bp_subtask_output]    前端: bpStore 存储输出
  │
  ├─ ⑭ emit ─────→ [bp_subtask_complete]  前端: SubtaskCompleteBlock
  │
  ├─ ⑮ emit ─────→ [bp_progress]          前端: TaskProgressCard 更新
  │                  statuses.research=done
  │
  └─ ⑯ return ───→ LLM 收到工具结果
                    [ai_text]              前端: LLM 回复文本
                    [done]                 前端: 流结束
```

## 4. 状态机

### 4.1 BP 实例状态 (BPInstanceSnapshot.status)

```
ACTIVE ──────→ COMPLETED    (最后一个子任务完成)
  │                ↑
  ├──→ SUSPENDED ──┘ resume  (bp_switch_task 切走再切回)
  │       │
  │       └──→ CANCELLED     (bp_cancel)
  └──────────→ CANCELLED
```

### 4.2 子任务状态 (SubtaskStatus)

```
PENDING ──→ CURRENT ──→ DONE
  ↑                       │
  │                       ▼
  └──── STALE ←───── (上游输出被编辑, mark_downstream_stale)
            │
            └──→ PENDING  (reset_stale_if_needed, bp_continue 时自动重置)

CURRENT ──→ FAILED  (超时或异常)
  │
  └──→ PENDING  (异常时重置, 可通过 bp_continue 重试)
```

### 4.3 枚举定义 (models.py)

| 枚举 | 值 | 说明 |
|------|---|------|
| `RunMode.MANUAL` | `"manual"` | 手动模式，每步暂停等用户确认 |
| `RunMode.AUTO` | `"auto"` | 自动模式，子任务完成后自动 bp_continue |
| `BPStatus.ACTIVE` | `"active"` | 实例正在执行 |
| `BPStatus.SUSPENDED` | `"suspended"` | 被 bp_switch_task 暂停 |
| `BPStatus.COMPLETED` | `"completed"` | 所有子任务完成 |
| `BPStatus.CANCELLED` | `"cancelled"` | 被 bp_cancel 取消 |
| `SubtaskStatus.PENDING` | `"pending"` | 等待执行 |
| `SubtaskStatus.CURRENT` | `"current"` | 正在执行 |
| `SubtaskStatus.DONE` | `"done"` | 已完成 |
| `SubtaskStatus.STALE` | `"stale"` | 上游输出被修改，需重新执行 |
| `SubtaskStatus.FAILED` | `"failed"` | 执行失败 |

---

## 5. 数据流转 (子任务间输入输出链)

```
initial_input ──→ SubTask 1 ──→ output_1
                  (idx=0)           │
                                    ▼
                  SubTask 2 ←── _resolve_input(idx=1)
                  (idx=1)         = subtask_outputs["st1"]
                     │
                     ▼
                  output_2
                     │
                     ▼
                  SubTask 3 ←── _resolve_input(idx=2)
                  (idx=2)         = subtask_outputs["st2"]
                     │            (或通过 input_mapping 指定任意上游)
                     ▼
                  output_3
```

**输入解析规则** (`engine._resolve_input`):
- `idx == 0` → 使用 `snap.initial_input`
- `idx > 0` 且有 `input_mapping` → 按映射从指定上游获取
- `idx > 0` 无映射 → 使用前一个子任务的 `subtask_outputs[prev.id]`

---

## 6. 关键模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| `BPEngine` | `bestpractice/engine.py` | 执行引擎：委派子任务、解析输出、发射 SSE 事件 |
| `BPToolHandler` | `bestpractice/handler.py` | 工具路由：7 个 bp_* 工具的入口分发 |
| `BPStateManager` | `bestpractice/state_manager.py` | 状态管理：实例生命周期、子任务状态、持久化 |
| `BPConfigLoader` | `bestpractice/config_loader.py` | 配置加载：从 YAML 加载 BP 模板 |
| `SchemaChain` | `bestpractice/schema_chain.py` | Schema 推导：根据上下游关系推导输出 schema |
| `ContextBridge` | `bestpractice/context_bridge.py` | 上下文桥接：BP 状态与 Agent 上下文的衔接 |
| `PromptAssembler` | `core/prompt_assembler.py` | 提示注入：`_build_bp_section()` 将 BP 状态注入 system prompt |
| `SeeCrabAdapter` | `api/adapters/seecrab_adapter.py` | SSE 桥接：`bp_*` 事件透传 (`etype.startswith("bp_")`) |
| `chatStore` | `stores/chat.ts` | 前端事件分发：dispatch bp_progress / bp_subtask_output / bp_subtask_complete |
| `bestPracticeStore` | `stores/bestPractice.ts` | 前端状态：instances Map、subtaskOutputs |
| `SubtaskCompleteBlock` | `components/chat/SubtaskCompleteBlock.vue` | UI：查看结果(纯UI) / 进入下一步(发消息) |
| `TaskProgressCard` | `components/chat/TaskProgressCard.vue` | UI：进度条 + 手动/自动切换 |
| `SubtaskOutputPanel` | `components/detail/SubtaskOutputPanel.vue` | UI：右侧面板子任务输出编辑 |

---

## 7. BP 工具一览 (7 个)

| 工具 | 触发场景 | 说明 |
|------|---------|------|
| `bp_start` | LLM 识别触发 / UI 点击 | 创建实例 + 执行第一个子任务 |
| `bp_continue` | 用户点击"进入下一步" / 自动模式 | 执行当前 index 的子任务 |
| `bp_get_output` | 用户想查看子任务输出 | 返回完整 JSON 输出 |
| `bp_edit_output` | 用户修改子任务输出 | 深度合并 + 标记下游 STALE |
| `bp_supplement_input` | 输入不完整时补充 | 合并到上游输出或 initial_input |
| `bp_switch_task` | 用户切换到另一个 BP | 暂停当前 + 恢复目标 |
| `bp_cancel` | 用户取消任务 | 标记为 CANCELLED |

---

## 8. System Prompt 注入机制

### 8.1 静态段 (system_static.md)

每次对话都注入，内容包括：
- 可用 BP 模板列表（名称、ID、触发方式、子任务流程）
- 触发规则（COMMAND / CONTEXT 关键词匹配）
- 交互规则（手动模式不调用 ask_user、自动模式立即 bp_continue）
- 子任务完成后的严格规则

### 8.2 动态段 (system_dynamic.md)

仅当 session 有 BP 实例时注入，内容包括：
- **status_table**: 所有实例的状态表（instance_id / BP名 / 状态 / 进度 / 模式）
- **active_context**: 当前活跃任务信息 + 冷却状态
- **intent_routing**: 暂停点意图路由（引导 LLM 识别用户意图并调用对应工具）

```
动态段示例:
| bp-a1b2c3d4 | 市场调研报告 | active | 1/3 | manual |

当前活跃任务: 市场调研报告 (进度: 1/3)

用户可能想要:
A) 修改上一步结果 (bp_edit_output)
B) 继续下一步 — 用户说「进入下一步」时立即调用 bp_continue
C) 切换到其他任务 (bp_switch_task)
D) 询问相关问题
E) 开始新话题
```

---

## 9. 前端按钮行为对照表

| 按钮 | 组件 | 点击行为 | 是否发消息 |
|------|------|---------|-----------|
| 自由模式 | BPTriggerBlock / AskUserBlock | 发送用户消息 → 普通对话 | ✅ |
| 最佳实践模式 | BPTriggerBlock / AskUserBlock | 发送用户消息 → LLM 调用 bp_start | ✅ |
| 查看结果 | SubtaskCompleteBlock | `uiStore.openSubtaskOutput()` 打开右侧面板 | ❌ 纯UI |
| 进入下一步 | SubtaskCompleteBlock | 发送 "进入下一步" → LLM 调用 bp_continue | ✅ |
| 查看最终报告 | SubtaskCompleteBlock (isLast) | `uiStore.openSubtaskOutput()` 打开右侧面板 | ❌ 纯UI |
| TaskProgressCard 点击 | TaskProgressCard | 打开右侧面板定位到当前子任务 | ❌ 纯UI |
| MANUAL/AUTO 切换 | TaskProgressCard | `bpStore.toggleRunMode()` + PUT /api/bp/run-mode | ❌ 纯UI |

---

## 10. 手动模式核心设计总结

**Pull 模型**: 用户掌握每一步的推进权。

1. 后端执行完子任务后，通过 `bp_subtask_complete` SSE 事件通知前端渲染 `SubtaskCompleteBlock`
2. LLM 收到工具结果后被告知"等待用户操作"，不主动推进，不调用 ask_user
3. 用户通过点击按钮"拉取"下一步 → 发送消息 "进入下一步" → LLM 调用 `bp_continue`
4. "查看结果"是纯前端操作，不经过 LLM，直接打开右侧面板展示可编辑输出

**与自动模式的区别**: 自动模式下 `_format_subtask_complete_result` 返回的工具结果文本会指示 LLM "立即调用 bp_continue"，LLM 不等待用户，直接连续执行所有子任务。
