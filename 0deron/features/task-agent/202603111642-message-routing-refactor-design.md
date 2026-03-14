# 消息路由与处理逻辑重构设计

> 日期: 2026-03-11
> 状态: 已确认

## 1. 目标

重构用户消息的路由和处理逻辑，实现清晰的两层架构：Router（路由判断）+ Handler（逻辑分发），统一所有处理路径的上下文管理和 SSE 输出。

## 2. 整体架构

```
用户消息 → API (SSE)
              │
              ▼
        MasterAgent（入口协调器）
              │
              ├── 构建 RoutingContext
              ├── MessageRouter（单次 LLM 调用）
              │     输出: RouteResult
              └── 分发
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
     agent_loop           TaskHandler
     (统一 Agent)          (逻辑分发层)
     ├─ system prompt           │
     ├─ tools/skills       ┌────┼────────────┐
     ├─ MCPs               ▼    ▼            ▼
     ├─ messages        new_task recall   step_interaction
     └─ yield SSE         │      │       ├─ edit_output
                           │      │       ├─ chat
                           │      │       └─ confirm_run
                           ▼      ▼            │
                      Orchestrator          用户确认后
                      (Linear impl,         ↓
                       Base 接口)       SubAgentWorker
                                        (JIT 配置, 执行单步)
```

### 核心原则

1. MasterAgent 是唯一入口，不直接处理业务逻辑
2. agent_loop 和 TaskHandler 是两条平行的处理管道
3. SubAgentWorker **仅在用户确认后**启动
4. 所有路径最终 yield 统一格式的 SSE 事件
5. **每步必须用户确认才运行**，永不自动推进执行
6. 前端直接操作（如点击 BP 入口、面板编辑输出）通过专用 API 端点处理，不经过 MessageRouter
7. API 层支持 `force_route` 参数，指定后跳过 Router 直接进入指定处理管道（用于"自由探索"等场景）

## 3. 路由器设计

### 3.1 RoutingContext（路由器输入）

```python
@dataclass
class RoutingContext:
    session_id: str

    # 静态信息（启动时加载）
    system_intro: str                          # 系统介绍摘要
    available_templates: list[TemplateSummary]  # BP 模板列表
    #   TemplateSummary: {id, name, description, trigger_keywords}

    # 活跃任务浅层摘要
    active_task: TaskSummary | None
    #   TaskSummary: {task_id, name, description, current_step_index,
    #                 steps: [{step_id, name, status}]}

    # 非活跃历史任务（最多10个）
    inactive_tasks: list[TaskBrief]            # {task_id, name, status}

    # 最近消息
    recent_messages: list[dict]                # 最近 N 条
```

### 3.2 RouteResult（路由器输出）

```python
class RouteType(Enum):
    AGENT_LOOP = "agent_loop"
    NEW_TASK = "new_task"
    RECALL_TASK = "recall_task"
    STEP_INTERACTION = "step_interaction"

class StepIntent(Enum):
    EDIT_OUTPUT = "edit_output"
    CHAT = "chat"
    CONFIRM_RUN = "confirm_run"

@dataclass
class RouteResult:
    route_type: RouteType
    template_id: str | None = None        # NEW_TASK 时
    task_id: str | None = None            # RECALL_TASK / STEP_INTERACTION 时
    step_id: str | None = None            # STEP_INTERACTION 时
    step_intent: StepIntent | None = None # STEP_INTERACTION 时
    message: str = ""                      # 路由器附带的说明
```

### 3.3 四个路由类型

| 路由 | 输出字段 | 处理方式 |
|------|---------|---------|
| `agent_loop` | 无 | Agent 完整推理，yield SSE |
| `new_task` | template_id | **直接入口**（前端点击 BP）: 创建任务 → 展示第一步输入 → 等用户；**消息路由入口**: 展示推荐 → 用户确认后创建 |
| `recall_task` | task_id | 恢复任务 → 提醒用户 |
| `step_interaction` | task_id + step_id + step_intent | 见 step_intent 表 |

### 3.4 三种 step_intent

| 意图 | 处理 | 是否启动 SubAgentWorker |
|------|------|----------------------|
| `edit_output` | 轻量 LLM 编辑步骤输出 | 否 |
| `chat` | 轻量 LLM 就步骤内容回答 | 否 |
| `confirm_run` | 构建 Payload → 执行 → 完成后展示下一步等确认 | **是** |

### 3.5 路由 Prompt 结构

```yaml
# unified_route.yaml v3.0
system: |
  你是消息路由器。根据上下文判断用户消息属于哪个类别，输出 JSON。

  ## 路由类别（优先级从高到低）
  1. step_interaction: 用户消息针对某个任务的某个步骤
     - 需要识别 task_id, step_id, step_intent
     - 特殊规则: 当有活跃任务且存在待执行步骤时，"继续"/"执行"/"下一步"/"确认"
       等模糊指令视为 step_interaction(confirm_run)，target 为当前待执行步骤
  2. recall_task: 用户提到了某个历史任务，想恢复它
     - 需要识别 task_id
  3. new_task: 用户的需求匹配了某个最佳实践模板
     - 需要识别 template_id
  4. agent_loop: 其他所有情况

  ## 输出格式
  {"route": "...", "task_id": "...", "step_id": "...",
   "step_intent": "...", "template_id": "...", "message": "..."}
```

## 4. TaskHandler 处理流程

TaskHandler 是逻辑分发层，根据 RouteResult 调用对应处理方法。

### 4.1 new_task

分两种入口：

#### 4.1.1 直接创建（前端点击 BP 入口，不经过 Router）

```
前端点击最佳实践 → POST /api/tasks/create
  │
  ▼
TaskHandler.new_task():
  1. orchestrator.create_task(session_id, template_id)
     → 创建 OrchestrationTask，所有步骤状态为 PENDING
  2. 取出第一个步骤的 input_schema
  3. yield SSE: task_created {task_id, task_name, steps_overview}
  4. yield SSE: step_input_required {step_id, step_name, input_schema}
     → 前端渲染任务卡片 + 第一步输入表单
  （不启动 SubAgentWorker，等用户填写并确认）
```

#### 4.1.2 建议创建（Router 判定 new_task，需用户确认）

```
RouteResult(NEW_TASK, template_id="api-design-v1")
  │
  ▼
TaskHandler.suggest_task():
  1. 加载模板信息 (name, description)
  2. yield SSE: task_suggested {template_id, task_name, description}
     → 前端渲染推荐卡片: [自由探索] / [使用最佳实践]
  3. 等待用户选择:
     - [使用最佳实践] → 前端调用 POST /api/tasks/create → 走 4.1.1 流程
     - [自由探索] → 前端携带 force_route=agent_loop 重新提交原消息，API 层跳过 Router 直接进入 agent_loop
```

### 4.2 recall_task

```
RouteResult(RECALL_TASK, task_id="task-123")
  │
  ▼
TaskHandler.recall_task():
  1. orchestrator.get_task(task_id)
  2. yield SSE: task_recalled {task_id, task_name, current_step, steps_summary}
     → 前端提示用户该任务已恢复，展示进度
```

### 4.3 step_interaction

```
RouteResult(STEP_INTERACTION, task_id, step_id, step_intent)
  │
  ▼
TaskHandler.step_interaction():
  │
  ├─ 前置检查: 目标步骤是否正在执行（status == RUNNING）?
  │   ├─ EDIT_OUTPUT / CONFIRM_RUN → 拒绝，yield SSE: text_delta "该步骤正在执行中，请等待完成"
  │   ├─ CHAT → yield SSE: text_delta "该步骤正在执行中，完成后可查看结果"
  │   └─ 返回，不继续分发
  │
  ├─ EDIT_OUTPUT:
  │   1. 取出该步骤的当前 output
  │   2. 根据用户消息修改 output（轻量 LLM 调用）
  │   3. orchestrator.update_step_output(task_id, step_id, new_output)
  │   4. yield SSE: step_output_updated {step_id, new_output}
  │
  ├─ CHAT:
  │   1. 构建上下文：任务摘要 + 该步骤详情 + 用户消息
  │   2. 调用轻量 LLM 回复（不执行任务，只回答问题）
  │   3. yield SSE: text_delta 流式输出
  │
  └─ CONFIRM_RUN:
      1. 从 orchestrator 获取已保存的步骤 input（由 4.5 步骤输入提交 API 存入）
      2. 构建 SubAgentPayload（JIT 注入配置）
      3. orchestrator.mark_step_running(task_id, step_id)
      4. 启动 SubAgentWorker.execute()
      5. yield SSE: step_started {step_id}
         → 执行过程中流式输出:
           yield SSE: text_delta（推理文本）
           yield SSE: tool_call_started {tool_name, tool_input}
           yield SSE: tool_call_completed {tool_name, tool_output}
      6. 成功时:
         a. orchestrator.mark_step_completed(task_id, step_id, output)
         b. yield SSE: step_completed {step_id, output}
         c. 生成 output_summary（轻量 LLM 摘要）
         d. 如果还有下一步:
            i.  获取下一步的 input_schema
            ii. 根据当前步骤 output + 下一步 input_schema，生成 prefilled_input（轻量 LLM，见 7.7）
                → 能从上一步输出提取的字段自动填入，无法提取的留空（统一逻辑）
            iii. yield SSE: step_input_required {next_step_id, next_step_name, input_schema, prefilled_input}
                → 前端渲染表单：已有值的字段预填充，用户确认/修改后走 4.5 → 确认后再次进入 CONFIRM_RUN
         e. 如果是最后一步:
            → yield SSE: task_completed {task_id}
      7. 失败时:
         a. orchestrator.mark_step_failed(task_id, step_id, error)
         b. yield SSE: step_failed {step_id, error_message}
         c. 不自动重试，等待用户决定（可重新 confirm_run 或修改输入）
```

### 4.4 步骤输出直接编辑（非消息路由路径）

用户在右侧面板内直接编辑步骤输出，通过专用 API 端点提交，不经过 MessageRouter。

```
用户在右侧面板编辑输出 → PUT /api/tasks/{task_id}/steps/{step_id}/output
  │
  ▼
TaskHandler.update_step_output_direct():
  1. 校验 task_id, step_id 合法性
  2. orchestrator.update_step_output(task_id, step_id, new_output)
  3. 重新生成 output_summary（轻量 LLM 摘要）
  4. yield SSE: step_output_updated {step_id, new_output, new_summary}
```

> **与 `step_interaction(edit_output)` 的区别**:
> - **面板直接编辑**: 用户自行修改内容，直接提交，不需要 LLM 理解意图
> - **消息路由编辑**: 用户用自然语言描述修改要求，需要 LLM 解读并执行修改

### 4.5 步骤输入提交（非消息路由路径）

用户填写步骤输入表单后点击 [确认提交]，通过专用 API 端点保存输入数据，不直接启动执行。

```
用户填写表单 → POST /api/tasks/{task_id}/steps/{step_id}/input
  │
  ▼
TaskHandler.submit_step_input():
  1. 校验 input 数据符合 input_schema
  2. orchestrator.save_step_input(task_id, step_id, input_data)
  3. yield SSE: step_input_saved {step_id, input_data}
  4. yield SSE: step_confirm_required {step_id, step_name}
     → 前端显示确认提示: "确认执行步骤 X？[确认执行] [修改输入]"
  5. 用户点击 [确认执行]:
     → 前端发送 POST /api/tasks/{task_id}/steps/{step_id}/run
     → 走 4.3 CONFIRM_RUN 流程
```

> **注意**: [确认执行] 是前端直接 API 调用（非消息路由），与原则 6 一致。
> 路由器触发的 `confirm_run` 同样适用于用户在聊天中说"执行"/"确认"等自然语言指令。

## 5. BaseOrchestrator 接口

```python
class BaseOrchestrator(ABC):
    """编排器基类，预留线性/图扩展"""

    # --- 任务生命周期 ---
    @abstractmethod
    async def create_task(self, session_id: str, template_id: str,
                          user_inputs: dict | None = None) -> OrchestrationTask: ...

    @abstractmethod
    async def get_task(self, task_id: str) -> OrchestrationTask | None: ...

    @abstractmethod
    async def get_session_tasks(self, session_id: str) -> list[OrchestrationTask]: ...

    @abstractmethod
    async def activate_task(self, task_id: str) -> None: ...
    # 同时将同 session 内其他 active 任务标记为 inactive

    # --- 步骤流转 ---
    @abstractmethod
    async def get_next_step(self, task_id: str) -> TaskStep | None: ...

    @abstractmethod
    async def save_step_input(self, task_id: str, step_id: str,
                               input_data: dict) -> None: ...

    @abstractmethod
    async def mark_step_running(self, task_id: str, step_id: str) -> None: ...

    @abstractmethod
    async def mark_step_completed(self, task_id: str, step_id: str,
                                   output: Any) -> None: ...

    @abstractmethod
    async def mark_step_failed(self, task_id: str, step_id: str,
                                error: str) -> None: ...

    @abstractmethod
    async def update_step_output(self, task_id: str, step_id: str,
                                  new_output: Any) -> None: ...


class LinearOrchestrator(BaseOrchestrator):
    """线性编排器，当前实现"""
    # get_next_step: 返回 current_step_index + 1

# 未来:
# class GraphOrchestrator(BaseOrchestrator):
#     # get_next_step: 根据 DAG 依赖 + 条件分支决定
```

### 任务激活/停用规则

- 每个 session 同一时间只有一个 `active_task`
- `create_task` 时：新任务标记为 active，当前 active_task（如有）自动标记为 inactive
- `recall_task` 时：被 recall 的任务标记为 active，当前 active_task 标记为 inactive
- `task_completed` 时：active_task 清空
- `activate_task()` 内部处理互斥逻辑

## 6. 步骤输出三层存储

当步骤输出涉及文件时，分三层存储：

```
步骤执行完成后：

1. output_result    — 完整输出结果 dict（存数据库）
                      → 前端显示用

2. output_summary   — LLM 自动生成摘要（几百字以内）
                      → 传入下一步的 LLM 上下文
                      → 节省 token

3. output_artifacts — 文件引用列表 [{path, type, name}]
                      → 传入下一步的 SubAgentPayload.artifacts
                      → SubAgentWorker 通过 file_tool 按需读取
```

### 下一步 SubAgentWorker 接收的上下文：

```
Messages:
  ├─ previous_steps_summary（LLM 生成的各步摘要）
  │     "第1步[API设计]输出摘要：定义了3个RESTful端点..."
  ├─ artifacts（文件引用，不是内容）
  │     [{path, type, name}, ...]
  └─ user_input + input_schema

Tools:
  └─ file_tool（需要完整内容时自己读文件）
```

## 7. 所有 LLM 调用点及上下文

### 7.1 MessageRouter（路由判断）

- **调用方式：** `brain.think_lightweight()` 单次，非流式
- **上下文：**

```
System Prompt:
  └─ unified_route.yaml（路由规则 + 输出格式）

User Prompt（拼接的 RoutingContext）:
  ├─ 可用 BP 模板列表 [{id, name, description, trigger_keywords}]
  ├─ 活跃任务浅层摘要（如有）
  │     {task_id, name, description, current_step_index, steps: [{step_id, name, status}]}
  ├─ 非活跃历史任务（最多10个）[{task_id, name, status}]
  ├─ 最近 N 条消息 [{role, content}]
  └─ 当前用户消息
```

- **不包含：** tools、skills、MCPs、完整对话历史、步骤输入输出内容

### 7.2 agent_loop（通用对话/复杂推理）

- **调用方式：** `Agent.chat_with_session_stream()` 流式，完整 agent 循环
- **上下文：**

```
System Prompt:
  ├─ identity/SOUL.md（核心人格）
  ├─ identity/AGENT.md（行为规范）
  └─ identity/USER.md（用户画像）

Tools:
  ├─ 系统工具（shell, file, web, ask_user...）
  ├─ Skills → tool schema
  └─ MCP servers → tool schema

Messages:
  └─ 完整 session_messages（当前会话全部对话历史）

Memory:
  └─ MemoryManager 注入的长期记忆
```

- **不包含：** 任务上下文、步骤详情

### 7.3 step_interaction / edit_output（编辑步骤输出）

- **调用方式：** `brain.think_lightweight()` 或轻量流式
- **上下文：**

```
System Prompt:
  └─ 编辑指令（"根据用户要求修改以下内容"）

User Prompt:
  ├─ 任务摘要 {task_name, description}
  ├─ 目标步骤 {step_name, step_description}
  ├─ 该步骤当前 output（完整内容）
  └─ 用户的编辑要求（当前消息）
```

- **不包含：** tools、skills、MCPs、其他步骤输出、完整对话历史

### 7.4 step_interaction / chat（就步骤内容聊天）

- **调用方式：** 轻量流式，yield text_delta
- **上下文：**

```
System Prompt:
  └─ 问答指令（"回答用户关于以下任务步骤的问题"）

User Prompt:
  ├─ 任务摘要 {task_name, description}
  ├─ 目标步骤 {step_name, step_description, status}
  ├─ 该步骤的 output（如已完成）
  ├─ 该步骤的 input（如已有）
  ├─ 前置步骤输出摘要 [{step_name, output_summary}]
  └─ 用户的问题（当前消息）
```

- **不包含：** tools、skills、MCPs、完整对话历史

### 7.5 SubAgentWorker / confirm_run（执行任务步骤）

- **调用方式：** 临时 Agent 实例，完整 agent 循环，流式
- **上下文：**

```
System Prompt（JIT 注入，来自 StepTemplate.sub_agent_config）:
  └─ sub_agent_config.system_prompt

Tools（JIT 注入）:
  ├─ sub_agent_config.tools
  ├─ sub_agent_config.skills → tool schema
  └─ sub_agent_config.mcps → tool schema

Messages（SubAgentPayload）:
  ├─ previous_steps_summary（前置步骤的 LLM 摘要）
  ├─ artifacts（文件引用列表，非内容）
  ├─ task_context（任务级共享变量）
  ├─ user_input
  └─ input_schema
```

- **不包含：** 完整 session 对话历史、其他任务信息、系统级 identity

### 7.6 output_summary（步骤输出摘要生成）

- **调用方式：** `brain.think_lightweight()` 单次，非流式
- **上下文：**

```
System Prompt:
  └─ 摘要指令（"将以下任务步骤的输出精炼为简明摘要"）

User Prompt:
  ├─ 步骤名称和描述
  ├─ 步骤完整 output_content
  └─ 产物列表 [{path, type, name}]
```

- **不包含：** tools、对话历史、其他步骤信息

### 7.7 input_prefill（步骤输入预填充生成）

- **调用方式：** `brain.think_lightweight()` 单次，非流式
- **上下文：**

```
System Prompt:
  └─ 预填充指令（"根据上一步的输出，为下一步的输入字段提供预填充值"）

User Prompt:
  ├─ 上一步名称和输出摘要（output_summary）
  ├─ 上一步完整输出（output_result）
  ├─ 下一步名称和描述
  └─ 下一步的 input_schema（字段定义）
```

- **输出：** `{field_name: prefilled_value | null, ...}` — 能从上一步输出中提取的字段填入值，无法提取的留 null
- **不包含：** tools、对话历史、其他步骤信息

### 7.8 汇总对比

| LLM 调用点 | 调用方式 | System Prompt | 对话历史 | 任务上下文 | Tools |
|-----------|---------|--------------|---------|-----------|-------|
| **Router** | 单次轻量 | 路由规则 yaml | 最近N条 | 浅层摘要 | 无 |
| **agent_loop** | 完整 agent | identity 三件套 | 完整 session | 无 | 全量 |
| **edit_output** | 单次轻量 | 编辑指令 | 无 | 当前步骤 output | 无 |
| **chat** | 轻量流式 | 问答指令 | 无 | 步骤详情+前置摘要 | 无 |
| **SubAgentWorker** | 完整 agent | 步骤专用 prompt | 无（仅前置摘要） | 摘要+产物引用 | 步骤配置子集 |
| **output_summary** | 单次轻量 | 摘要指令 | 无 | 当前步骤完整输出 | 无 |
| **input_prefill** | 单次轻量 | 预填充指令 | 无 | 上一步输出+下一步 schema | 无 |

## 8. 与当前代码的变更对比

| 维度 | 当前实现 | 重构后 |
|------|---------|--------|
| 路由类型 | 6 种（PLAIN_TEXT, AGENT_LOOP, NEW_BP, RECALL_TASK, HIT_STEP, TASK_RUNNING） | 4 种（agent_loop, new_task, recall_task, step_interaction） |
| 非任务处理 | PLAIN_TEXT + AGENT_LOOP 两条路径 | 合并为 agent_loop 一条 |
| TASK_RUNNING | 独立路由，分 related/unrelated | 不再存在，步骤状态由 TaskHandler 内部处理 |
| step_intent | HIT_STEP 内 modify_output / execute_step | step_interaction 内 edit_output / chat / confirm_run |
| 路由器输出 | 子类型 + ID 分开判断 | 一次 LLM 输出子类型 + ID + intent |
| 步骤输出传递 | previous_steps_summary (文本) | 三层：content + summary + artifacts |
| Orchestrator | 单一 TaskOrchestrator | BaseOrchestrator 接口 + LinearOrchestrator 实现 |
