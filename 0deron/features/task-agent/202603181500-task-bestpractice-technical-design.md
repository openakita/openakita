# 最佳实践任务管理系统 — 技术设计方案

> 需求文档: `202603171459-task-bestpractice-requirement-structured.md`
> 交互模拟: `mockup-bestpractice-interaction.html`
> 设计日期: 2026-03-18

---

## 1. 概述

### 1.1 设计目标

在现有 SeeAgent 多 Agent 体系之上，新增 `bestpractice` 模块，实现业务沉淀的任务模板编排。核心设计约束：

| 约束 | 策略 |
|------|------|
| 高内聚低耦合 | 独立 `bestpractice/` 包，通过 3 个明确接口与主系统交互 |
| 复用已有能力 | 子任务执行复用 `AgentOrchestrator.delegate()`，事件流复用 `_sse_event_bus` |
| Prompt 可维护 | 独立 `.md` 模板文件，`string.Template` 变量注入 |
| KV-Cache 友好 | 系统提示按 静态→半静态→动态 排列，动态段置于末尾 |

### 1.2 与现有系统的 3 个交互面

```
bestpractice 模块

  ① PromptAssembler ─── 注入 BP 提示段到系统提示
  ② AgentOrchestrator ── 委派子任务给 SubAgent
  ③ SSE EventBus ─────── 发射 bp_* 事件到前端
```

不修改 `AgentState`、`TaskState`、`SessionContext` 的核心结构。

### 1.3 与现有组件的关系

BP 模块引入的新组件（`BPToolHandler`、`BPEngine`、`BPStateManager`）与现有多 Agent 体系中的 `AgentToolHandler`、`AgentOrchestrator` 的关系如下：

```
SystemHandlerRegistry (工具路由)
  │
  ├── AgentToolHandler                    ← 现有，处理 delegate_to_agent / spawn_agent / create_agent
  │     └── 调用 → AgentOrchestrator.delegate()
  │
  ├── BPToolHandler (新增)                ← 新增，处理 bp_start / bp_continue / bp_edit_output / bp_switch_task
  │     ├── 调用 → BPEngine（编排逻辑）
  │     │     └── 调用 → AgentOrchestrator.delegate()  ← 复用同一条执行路径
  │     └── 读写 → BPStateManager（状态管理）
  │
  ├── FilesystemHandler                   ← 现有
  ├── BrowserHandler                      ← 现有
  └── ...
```

**核心设计决策**：

| 关系 | 说明 |
|------|------|
| BPToolHandler **并列于** AgentToolHandler | 都是 `SystemHandlerRegistry` 下的独立 handler，互不调用、互不依赖。两者的 `handle()` 签名均为 `(tool_name, params) -> str`，通过 `agent._current_session` 获取 session，通过 `seeagent.main._orchestrator` 获取 orchestrator |
| BPEngine **组合** AgentOrchestrator | BPEngine 通过 `orchestrator.delegate()` 委派子任务，底层走完全相同的 SubAgent 创建→ReAct 执行→结果返回 路径 |
| BPStateManager **独立于** AgentState | 不继承、不扩展 `AgentState`/`TaskState`/`SessionContext`，是 BP 专属的状态容器 |
| BPToolHandler 是**薄层** | 仅做参数解析和调用转发，业务逻辑全部在 `BPEngine` 中。构造时接收 `agent` 引用（与 `AgentToolHandler` 一致），`BPEngine` 和 `BPStateManager` 延迟初始化 |

**为什么不复用 `AgentToolHandler._delegate()`**：
- `AgentToolHandler._delegate()` 是为 LLM 主动发起的通用委派设计的（参数来自 LLM 推理）
- BP 子任务委派需要 BPEngine 在前后注入额外逻辑（Schema 推导、输出解析、进度事件、状态更新），属于「编排级」调用
- 两者共享的是底层的 `AgentOrchestrator.delegate()` —— 这是正确的复用层级

---

## 2. 模块结构

### 2.1 目录布局

```
src/seeagent/bestpractice/
├── __init__.py               # 公开 API：BPEngine, BPStateManager, get_bp_engine
├── config.py                 # 配置模型（YAML → dataclass）
├── engine.py                 # BPEngine — 主编排引擎
├── state_manager.py          # BPStateManager — 实例生命周期与状态
├── schema_chain.py           # Schema 推导链逻辑
├── trigger.py                # 触发检测（COMMAND/EVENT/CONTEXT/UI_CLICK）
├── context_bridge.py         # 上下文桥接（压缩/恢复/注入）
├── prompt_loader.py          # Prompt 模板加载器
└── prompts/                  # Prompt 模板文件
    ├── system_static.md      # 系统提示 — 静态段（可缓存）
    ├── system_dynamic.md     # 系统提示 — 动态段（每次请求）
    ├── subtask_instruction.md # SubAgent 子任务指令
    ├── intent_router.md      # 意图路由（触发/编辑/切换/追问）
    ├── chat_to_edit.md       # Deep Merge 指令
    ├── context_restore.md    # 上下文恢复注入
    └── cascade_confirm.md    # 级联重跑确认

src/seeagent/tools/handlers/
└── bestpractice.py           # BPToolHandler — 工具处理器（薄层）
```

### 2.2 依赖关系（单向）

```
                    ┌── schema_chain.py
                    │        │
                    │        ▼
                    ├── config.py (数据模型)
                    │        ▲
                    │        │
                    ├── state_manager.py
                    │        ▲
                    │        │
                    ├── engine.py ──→ AgentOrchestrator (已有)
                    │     │  │
                    │     │  └──→ context_bridge.py ──→ ContextManager (已有)
                    │     │
                    │     └──→ prompt_loader.py
                    │
                    ├── trigger.py ──→ config.py
                    │
SystemHandlerRegistry (已有)
    └── tools/handlers/bestpractice.py ──→ engine.py + state_manager.py
```

- `engine.py` 是唯一的 "胖" 模块，依赖多个内部模块 + 外部 `AgentOrchestrator`
- `BPToolHandler` 在 `SystemHandlerRegistry` 中注册，与 `AgentToolHandler` 并列
- `trigger.py` 仅依赖 `config.py`（读取触发配置），不依赖 engine 或 prompt_loader

---

## 3. 数据模型

### 3.1 配置模型 (`config.py`)

```python
from dataclasses import dataclass, field
from enum import Enum


class RunMode(str, Enum):
    MANUAL = "manual"
    AUTO = "auto"


class BPStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"


class SubtaskStatus(str, Enum):
    PENDING = "pending"
    CURRENT = "current"
    DONE = "done"
    STALE = "stale"


@dataclass
class SubtaskConfig:
    id: str
    name: str
    agent_profile: str                          # → AgentProfile.id
    input_schema: dict                          # JSON Schema
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    # 上游子任务 ID 列表。为空时按 subtasks 列表顺序隐式推导（线性模式）。
    # DAG 扩展预留：显式声明后启用拓扑排序调度和并行执行。
    input_mapping: dict[str, str] = field(default_factory=dict)
    # 输入字段到上游子任务输出的映射，例: {"market_data": "research"}
    # 为空时默认取前一个子任务的完整 output（线性模式兼容）。
    # DAG 扩展预留：支持 fan-in，从多个上游子任务汇聚输入。


@dataclass
class TriggerConfig:
    type: str                                   # command / event / context / schedule / ui_click
    pattern: str | None = None                  # COMMAND 模式匹配
    event: str | None = None                    # EVENT 事件名
    conditions: list[str] | None = None         # CONTEXT 关键词
    cron: str | None = None                     # CRON 表达式


@dataclass
class BestPracticeConfig:
    id: str
    name: str
    description: str
    subtasks: list[SubtaskConfig]
    triggers: list[TriggerConfig] = field(default_factory=list)
    final_output_schema: dict | None = None     # 可选，最终输出格式
    default_run_mode: RunMode = RunMode.MANUAL
```

### 3.2 运行态模型 (`state_manager.py`)

```python
@dataclass
class BPInstanceSnapshot:
    """单个 BestPractice 实例的完整状态快照"""

    # 身份
    bp_id: str                                  # BestPracticeConfig.id
    instance_id: str                            # 运行时唯一 ID (uuid4)
    session_id: str                             # 所属会话

    # 生命周期
    status: BPStatus = BPStatus.ACTIVE
    created_at: float = 0.0
    completed_at: float | None = None             # 完成时间（与存储设计对齐）
    suspended_at: float | None = None

    # 执行进度
    current_subtask_index: int = 0
    # 线性模式下的便捷指针。DAG 模式下由 subtask_statuses 推导，此字段退化为辅助。
    run_mode: RunMode = RunMode.MANUAL
    subtask_statuses: dict[str, SubtaskStatus] = field(default_factory=dict)
    # subtask_id → status — 这是执行进度的**主真值源**
    # 线性模式：与 current_subtask_index 保持同步
    # DAG 模式：完全依赖此字段判断 ready/completed/stale 集合

    # 数据
    subtask_outputs: dict[str, dict] = field(default_factory=dict)
    # subtask_id → output JSON

    # 上下文
    context_summary: str = ""                   # 挂起时 LLM 压缩生成
    master_messages_snapshot: list[dict] | None = None
    # 挂起时 MasterAgent.Context.messages 的副本（可选，用于精确恢复）

    # 配置引用
    bp_config: BestPracticeConfig | None = None
```

### 3.3 配置加载 (`config.py`)

```python
class BPConfigLoader:
    """从 YAML 文件发现并加载 BestPractice 配置"""

    def __init__(self, search_paths: list[Path]):
        # 默认搜索路径：
        # 1. src/seeagent/bestpractice/configs/  （系统内置）
        # 2. {project_root}/bestpractice/          （用户自定义）
        self._search_paths = search_paths
        self._configs: dict[str, BestPracticeConfig] = {}

    def load_all(self) -> dict[str, BestPracticeConfig]:
        """扫描所有路径，解析 YAML → BestPracticeConfig"""

    def get(self, bp_id: str) -> BestPracticeConfig | None:
        """按 ID 获取配置"""
```

### 3.4 组件生命周期与所有权

```
初始化链（应用启动时）:

Agent.__init__()
  │
  ├─ BPToolHandler(agent)          ← 注册到 SystemHandlerRegistry
  │     └─ 延迟初始化：首次工具调用时通过 get_bp_engine() 获取单例
  │
  └─ PromptAssembler(..., bp_engine=None)
        └─ bp_engine 延迟绑定：首次 build_system_prompt 调用时通过 get_bp_engine() 获取

首次 BP 工具调用时：

get_bp_engine()（模块级单例工厂）
  │
  ├─ BPStateManager()              ← 进程级单例
  │
  └─ BPEngine(state_manager)       ← 进程级单例
        ├─ 持有 BPStateManager
        ├─ 持有 BPConfigLoader
        ├─ 持有 PromptTemplateLoader
        ├─ 持有 SchemaChain
        └─ 持有 ContextBridge
```

**`get_bp_engine()` 单例工厂**：

```python
# bestpractice/__init__.py
_bp_engine: BPEngine | None = None

def get_bp_engine() -> BPEngine:
    """返回进程级 BPEngine 单例。BPToolHandler 和 PromptAssembler 共享同一实例。"""
    global _bp_engine
    if _bp_engine is None:
        state_manager = BPStateManager()
        _bp_engine = BPEngine(state_manager=state_manager)
    return _bp_engine
```

**所有权规则**：

| 组件 | 所有者 | 生命周期 | 说明 |
|------|--------|---------|------|
| `BPStateManager` | `get_bp_engine()` 单例工厂 | 进程级 | 管理所有会话的 BP 实例状态 |
| `BPEngine` | `get_bp_engine()` 单例工厂 | 进程级 | 编排逻辑，无状态（状态在 BPStateManager 中） |
| `BPToolHandler` | `SystemHandlerRegistry` | 进程级 | 薄层，通过 `get_bp_engine()` 获取共享 engine |
| `PromptAssembler` | `Agent` | 进程级 | 通过 `_get_bp_engine()` 延迟获取共享 engine |
| `BPConfigLoader` | `BPEngine` | 进程级 | 配置缓存，启动时加载 |
| `ContextBridge` | `BPEngine` | 进程级 | 无状态工具类 |

> **注意**：`BPToolHandler` 和 `PromptAssembler` 均采用延迟初始化模式，
> 通过 `get_bp_engine()` 模块级单例工厂获取同一个 `BPEngine` 实例。
> 避免在未启用 BP 功能时加载 bestpractice 包。

---

## 4. 存储设计

### 4.1 BPStateManager（核心状态管理器）

```python
class BPStateManager:
    """
    BestPractice 实例的生命周期与状态管理。

    设计决策：
    - 独立于 AgentState / TaskState / SessionContext
    - 内存存储，会话级生命周期
    - 后续可扩展为 SQLite 持久化
    """

    def __init__(self) -> None:
        self._instances: dict[str, BPInstanceSnapshot] = {}  # instance_id → snapshot
        self._session_index: dict[str, list[str]] = {}       # session_id → [instance_ids]
        self._active_map: dict[str, str | None] = {}          # session_id → active_instance_id
        self._lock = asyncio.Lock()  # 必须用 asyncio.Lock，系统全异步

    # ── 生命周期 ──

    def create_instance(
        self,
        bp_config: BestPracticeConfig,
        session_id: str,
    ) -> BPInstanceSnapshot:
        """创建新实例，设为该会话的活跃实例"""

    def suspend(
        self,
        instance_id: str,
        context_summary: str,
        master_messages: list[dict] | None = None,
    ) -> None:
        """挂起实例：保存上下文摘要，状态 → SUSPENDED"""

    def resume(self, instance_id: str) -> BPInstanceSnapshot:
        """恢复实例：状态 → ACTIVE，返回快照"""

    def complete(self, instance_id: str) -> None:
        """标记实例完成"""

    # ── 子任务数据 ──

    def advance_subtask(self, instance_id: str) -> str:
        """推进到下一个子任务，返回新的 subtask_id。
        线性模式：index + 1。
        DAG 扩展：从 ready 集合中按拓扑序取下一个。"""

    def update_subtask_output(
        self,
        instance_id: str,
        subtask_id: str,
        output: dict,
    ) -> None:
        """存储子任务输出 JSON"""

    def merge_subtask_output(
        self,
        instance_id: str,
        subtask_id: str,
        patch: dict,
    ) -> tuple[dict, dict]:
        """Deep merge 修改到子任务输出，返回 (修改前, 修改后)"""

    def update_subtask_status(
        self,
        instance_id: str,
        subtask_id: str,
        status: SubtaskStatus,
    ) -> None:
        """更新单个子任务的状态"""

    def mark_downstream_stale(
        self,
        instance_id: str,
        changed_subtask_id: str,
    ) -> list[str]:
        """将 changed_subtask_id 的所有下游子任务标记为 stale，返回被标记的 subtask_id 列表。
        线性模式：changed_subtask_id 之后的所有已完成子任务。
        DAG 扩展：BFS 遍历依赖图的所有可达下游节点。"""

    # ── 查询 ──

    def get(self, instance_id: str) -> BPInstanceSnapshot | None:
        """按 instance_id 获取实例快照"""

    def get_active(self, session_id: str) -> BPInstanceSnapshot | None:
        """获取会话的活跃实例"""

    def get_all_for_session(self, session_id: str) -> list[BPInstanceSnapshot]:
        """获取会话的所有实例"""

    def get_status_table(self, session_id: str) -> str:
        """生成系统提示注入的状态表文本"""

    def get_outputs_summary(self, instance_id: str) -> str:
        """生成子任务输出摘要（用于意图路由上下文）"""
```

### 4.2 存储层级关系

```
┌──────────────────────────────────────────────────────────────┐
│  Session 层（持久）                                           │
│                                                               │
│  SessionContext.messages (统一时间线)                           │
│  ├── 每条消息通过 **metadata 标记 bp_instance_id               │
│  └── 全量存储，支持审计回溯                                    │
│                                                               │
│  BPStateManager (BP 实例状态)                                  │
│  ├── _instances: { instance_id → BPInstanceSnapshot }         │
│  ├── _active_map: { session_id → active_instance_id }         │
│  └── 内存存储，独立于 SessionContext                           │
├───────────────────────────────────────────────────────────────┤
│  MasterAgent 层（运行时）                                      │
│                                                               │
│  Brain.Context.messages (LLM 工作窗口)                         │
│  ├── 同一时刻只包含活跃 BP 的对话                              │
│  ├── 子任务输出作为 tool_result 自然存在                       │
│  └── 切换任务时整体替换                                        │
├───────────────────────────────────────────────────────────────┤
│  SubAgent 层（临时）                                           │
│                                                               │
│  Brain.Context.messages (子任务执行上下文)                      │
│  ├── 仅含委派消息 + 子任务工具调用链                           │
│  ├── 生命周期：子任务开始 → 完成 → 销毁                       │
│  └── 结果以 tool_result 返回 MasterAgent                      │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. Prompt 模板设计

### 5.1 模板加载器 (`prompt_loader.py`)

```python
from pathlib import Path
from string import Template


class PromptTemplateLoader:
    """
    从文件加载 Prompt 模板，支持变量注入。

    使用 string.Template 语法（$variable 或 ${variable}），
    避免与 JSON 中的 {} 冲突。
    """

    _TEMPLATE_DIR = Path(__file__).parent / "prompts"

    def __init__(self) -> None:
        self._cache: dict[str, Template] = {}

    def load(self, name: str) -> Template:
        """加载模板（内存缓存）"""
        if name not in self._cache:
            path = self._TEMPLATE_DIR / f"{name}.md"
            text = path.read_text(encoding="utf-8")
            self._cache[name] = Template(text)
        return self._cache[name]

    def render(self, name: str, **kwargs) -> str:
        """加载 + 变量注入"""
        return self.load(name).safe_substitute(**kwargs)
```

### 5.2 系统提示注入 — KV-Cache 策略

现有系统提示的组装顺序（`prompt_assembler.py`）：

```
[1] base_prompt         ← STATIC  ─┐
[2] system_info         ← STATIC   │ KV-Cache 可命中区
[3] env_snapshot        ← DYNAMIC ─┘ ← 打断缓存
[4] skill_catalog       ← STATIC
[5] mcp_catalog         ← STATIC
[6] memory_context      ← DYNAMIC
[7] tools_text          ← STATIC
[8] tools_guide         ← STATIC
[9] core_principles     ← STATIC
[10] profile_prompt     ← DYNAMIC
```

**BP 注入策略**：在 `[9]` 和 `[10]` 之间插入 BP 段，分静态和动态两部分：

```
[1]  base_prompt         ← STATIC  ─┐
[2]  system_info         ← STATIC   │
[3]  env_snapshot        ← DYNAMIC ─┘
[4]  skill_catalog       ← STATIC  ─┐
[5]  mcp_catalog         ← STATIC   │ 次级缓存区
[6]  memory_context      ← DYNAMIC ─┘
[7]  tools_text          ← STATIC  ─┐
[8]  tools_guide         ← STATIC   │
[9]  core_principles     ← STATIC   │
[9.1] BP_STATIC_SECTION  ← STATIC ──┘ NEW: BP 规则 + 可用 BP 列表
[9.2] BP_DYNAMIC_SECTION ← DYNAMIC    NEW: BP 状态表 + 活跃上下文
[10] profile_prompt      ← DYNAMIC
```

> **注意**：受限于已有 `env_snapshot` 在 `[3]` 位置打断缓存，完整的前缀缓存仅 `[1]+[2]`。但 Anthropic API 的 prompt caching 支持多个 cache_control breakpoint，可在 `[9.1]` 末尾设置第二个缓存点，使得 `[4]~[9.1]` 的静态内容也能获得缓存命中。

### 5.3 模板文件设计

#### `system_static.md` — 注入到系统提示静态段

```markdown
## 最佳实践能力

你具备最佳实践任务编排能力。当检测到用户意图匹配以下最佳实践时，
使用 ask_user 工具让用户选择「自由模式」或「最佳实践模式」。

### 可用最佳实践

$available_practices

### 交互规则
1. **触发确认**：CONTEXT 触发时必须通过 ask_user 给用户选择权
2. **执行工具**：确认后调用 bp_start 启动，bp_continue 推进子任务
3. **输出编辑**：用户可通过自然语言修改已完成子任务输出（调用 bp_edit_output）
4. **任务切换**：用户可通过自然语言切换到其他 BP 实例（调用 bp_switch_task）
5. **手动模式**：子任务完成后展示 [查看结果] [进入下一步] 等待用户操作
6. **自动模式**：子任务完成后自动调用 bp_continue
```

#### `system_dynamic.md` — 注入到系统提示动态段

```markdown
## 当前最佳实践状态

$status_table

$active_context
```

#### `subtask_instruction.md` — SubAgent 委派消息

```markdown
你正在执行最佳实践「$bp_name」的子任务「$subtask_name」。

## 任务说明
$subtask_description

## 输入数据
```json
$input_json
```

## 输出要求

完成任务后，你必须：
1. 简要说明运行结果（文本摘要）
2. 按以下 Schema 整理结构化输出 JSON：

```json
$output_schema
```

严格按 Schema 的 required 字段输出，不要遗漏。
```

#### `intent_router.md` — 暂停期间意图检测（注入到 MasterAgent 上下文）

```markdown
[系统提示] 用户在最佳实践暂停期间发送了消息。请判断意图：

当前活跃任务：$active_task_info
已完成子任务输出摘要：
$outputs_summary

根据用户消息判断：
A) 修改某个子任务的输出 → 调用 bp_edit_output
B) 切换到其他任务 → 调用 bp_switch_task
C) 确认进入下一步 → 调用 bp_continue
D) 与当前任务相关的追问 → 直接回答
```

#### `chat_to_edit.md` — Deep Merge 指令

```markdown
用户要求修改子任务「$subtask_name」的输出。

当前输出：
```json
$current_output
```

修改意图：$user_message

请执行 deep merge：
- 仅修改用户明确提及的字段
- 未提及的字段保持原值不变
- 返回修改后的完整 JSON（保持原有 Schema 结构）
```

#### `context_restore.md` — 任务恢复注入

```markdown
[上下文恢复] 从挂起的任务「$bp_name」恢复：
$completed_subtasks
- 子任务 $next_index ~ $total 待执行
- 运行模式：$run_mode
- 上下文摘要：$context_summary
```

### 5.4 Prompt 与 KV-Cache 的关系图

```
LLM 请求结构:
┌──────────────────────────────────────────────────────┐
│ system_prompt                                         │
│ ┌─ [Cache Breakpoint 1] ───────────────────────────┐ │
│ │ base_prompt + system_info                         │ │ ← 前缀缓存（跨请求命中）
│ └──────────────────────────────────────────────────┘ │
│ env_snapshot (dynamic)                                │ ← 打断缓存
│ ┌─ [Cache Breakpoint 2] ───────────────────────────┐ │
│ │ skill_catalog + mcp_catalog + tools + principles  │ │
│ │ + BP_STATIC_SECTION                               │ │ ← 次级缓存（会话内命中）
│ └──────────────────────────────────────────────────┘ │
│ BP_DYNAMIC_SECTION + memory_context + profile_prompt │ ← 每次请求不同
└──────────────────────────────────────────────────────┘
│ messages[]                                            │
│ ├── [历史消息...] ← 增量增长，尾部可能触发压缩         │
│ └── [当前用户消息]                                     │
└──────────────────────────────────────────────────────┘
```

SubAgent 的 Prompt KV-Cache 优化：

```
SubAgent system_prompt:
┌─ [Cache Breakpoint] ──────────────────────────────┐
│ AgentProfile.custom_prompt                         │
│ + 通用 Agent 规则                                   │ ← 同一 Profile 跨子任务缓存
└───────────────────────────────────────────────────┘
subtask_instruction.md (含 input_json + output_schema) │ ← 每次子任务不同

SubAgent messages:
└── 仅委派消息 (单条) ← 无历史消息
```

---

## 6. 核心流程

### 6.1 触发与启动

```
用户输入
  │
  ▼
MasterAgent 推理循环 (现有 ReAct Loop)
  │
  ├─ 系统提示中包含 BP_STATIC（可用 BP 定义 + 触发条件）
  │
  ├─【COMMAND 触发】用户消息匹配 pattern
  │   └─ LLM 识别 → 调用 bp_start 工具
  │
  ├─【CONTEXT 触发】用户消息命中 conditions 关键词
  │   └─ LLM 识别 → 调用 ask_user（"自由模式" / "最佳实践模式"）
  │       ├─ "自由模式" → 设置 inferCooldown=5，正常对话
  │       └─ "最佳实践模式" → 调用 bp_start 工具
  │
  ├─【EVENT 触发】外部事件到达
  │   └─ 通过 pending_user_inserts 注入触发消息
  │       → MasterAgent 识别 → 调用 bp_start
  │
  └─【UI_CLICK 触发】前端直接调用
      └─ API 路由 → 构造 bp_start 调用
```

**`bp_start` 工具执行流程**：

```python
# tools/handlers/bestpractice.py — 简化示意
# BPToolHandler.handle(tool_name, params) 被 SystemHandlerRegistry 调用，
# session 和 orchestrator 在 handle() 内部通过 self._agent._current_session
# 和 seeagent.main._orchestrator 获取（与 AgentToolHandler 相同模式）。

async def _bp_start(self, args: dict, *, session, orchestrator) -> str:
    # 1. 加载配置
    config = self._engine.get_config(args["bp_id"])

    # 2. 验证 input_data vs 第一个子任务的 input_schema
    SchemaChain.validate_input(config.subtasks[0].input_schema, args.get("input_data", {}))

    # 3. 创建实例
    instance = self._state.create_instance(config, session.id)

    # 4. 发射 bp_progress 事件（前端渲染 TaskProgressCard）
    await self._engine.emit_progress(instance.instance_id, session)

    # 5. 执行第一个子任务
    result = await self._engine.execute_subtask(
        instance_id=instance.instance_id,
        orchestrator=orchestrator,
        session=session,
    )

    return result  # 返回给 MasterAgent 作为 tool_result
```

### 6.2 子任务执行

```
BPEngine.execute_subtask(instance_id)
  │
  ▼
1. 读取实例状态 → 确定当前子任务 (subtask_config)
  │
  ▼
2. 准备输入数据（通过 _resolve_input 抽象）：
   ├─ 线性模式：取前一个子任务的 output（或初始 input_data）
   └─ DAG 扩展预留：按 input_mapping 从多个上游子任务汇聚输入
  │
  ▼
3. 推导输出 Schema（schema_chain.py）：
   ├─ 非最后一个子任务 → output_schema = next_subtask.input_schema
   └─ 最后一个子任务   → output_schema = bp_config.final_output_schema || null
  │
  ▼
4. 渲染子任务指令（prompt_loader.render("subtask_instruction", ...)）
  │
  ▼
5. 更新子任务状态 → CURRENT
  │
  ▼
6. 发射 bp_progress 事件（进度更新）
  │
  ▼
7. 委派执行：
   result = await orchestrator.delegate(
       session=session,
       from_agent="main",
       to_agent=subtask_config.agent_profile,  # AgentProfile.id
       message=rendered_instruction,            # 含 input + output_schema
       reason=f"执行最佳实践「{bp_name}」子任务「{subtask_name}」",
   )
  │  ↓ SubAgent 执行（独立上下文，SSE 事件通过 event_bus 流出）
  │  ↓ 返回结果字符串
  │
  ▼
8. 解析结果 → 提取 output JSON
   ├─ 尝试从 result 中解析 JSON 块
   └─ 验证 vs output_schema（宽松模式，记录 warning）
  │
  ▼
9. 存储输出 → state_manager.update_subtask_output(instance_id, subtask_id, output)
  │
  ▼
10. 更新子任务状态 → DONE
  │
  ▼
11. 发射事件：
    ├─ bp_progress（进度推进）
    └─ bp_subtask_output（输出数据，前端渲染 SubtaskOutputPanel）
  │
  ▼
12. 判断下一步：
    ├─ 是最后一个子任务 → 标记实例 COMPLETED，返回最终结果
    ├─ auto 模式 → 递归调用 execute_subtask (下一个)
    └─ manual 模式 → 返回完成信息，等待 MasterAgent 展示按钮
```

**关键代码流**：

```python
# engine.py
class BPEngine:
    """
    BestPractice 主编排引擎。
    无状态（状态全部在 BPStateManager 中），进程级单例。
    通过 get_bp_engine() 工厂获取。

    注：BPEngine 的方法按功能分散在不同章节中定义：
    - §6.2: __init__, get_config, execute_subtask, reset_stale_if_needed,
            validate_output_soft, _resolve_input, 事件/格式化/切换 stubs
    - §9.2: _emit_event (SSE 事件发射的详细实现)
    - §12.1: get_static_prompt_section, get_dynamic_prompt_section
    实现时应合并为单个类。
    """

    def __init__(self, state_manager: "BPStateManager") -> None:
        self._state_manager = state_manager
        self._config_loader = BPConfigLoader(search_paths=[...])  # 系统内置 + 用户自定义
        self._config_loader.load_all()
        self._prompt_loader = PromptTemplateLoader()
        self._schema_chain = SchemaChain()
        self._context_bridge: ContextBridge | None = None  # 延迟创建（依赖 ContextManager）

    def get_config(self, bp_id: str) -> BestPracticeConfig | None:
        """按 ID 获取 BP 配置。"""
        return self._config_loader.get(bp_id)

    async def execute_subtask(
        self,
        instance_id: str,
        orchestrator: "AgentOrchestrator",
        session: "Session",
    ) -> str:
        instance = self._state_manager.get(instance_id)
        config = instance.bp_config
        idx = instance.current_subtask_index
        subtask = config.subtasks[idx]

        # 准备输入（抽象为 _resolve_input，线性/DAG 通用）
        input_data = self._resolve_input(instance, subtask)

        # 推导输出 Schema
        output_schema = self._schema_chain.derive_output_schema(config, idx)

        # 渲染委派消息
        message = self._prompt_loader.render(
            "subtask_instruction",
            bp_name=config.name,
            subtask_name=subtask.name,
            subtask_description=subtask.description,
            input_json=json.dumps(input_data, ensure_ascii=False, indent=2),
            output_schema=json.dumps(output_schema, ensure_ascii=False, indent=2)
                if output_schema else "由你自行决定合适的输出格式",
        )

        # 委派执行（复用已有机制）
        try:
            result = await orchestrator.delegate(
                session=session,
                from_agent="main",
                to_agent=subtask.agent_profile,
                message=message,
                reason=f"BP:{config.name} / {subtask.name}",
            )
        except Exception as e:
            # 委派失败：标记子任务为 PENDING（可重试），返回错误信息给 MasterAgent
            logger.error(f"SubTask delegation failed: {subtask.id} - {e}")
            self._state_manager.update_subtask_status(instance_id, subtask.id, SubtaskStatus.PENDING)
            return (
                f"子任务「{subtask.name}」执行失败: {e}\n"
                f"子任务已重置为 PENDING，可通过 bp_continue 重试。"
            )

        # 解析输出
        output = self._parse_output_json(result)
        self._state_manager.update_subtask_output(instance_id, subtask.id, output)

        # 发射事件
        await self._emit_bp_events(instance_id, subtask.id, output)

        # 判断下一步
        if idx >= len(config.subtasks) - 1:
            self._state_manager.complete(instance_id)
            return self._format_completion_result(instance)

        self._state_manager.advance_subtask(instance_id)

        if instance.run_mode == RunMode.AUTO:
            # 自动模式：递归执行下一个（中间检查 cancel_event）
            task_state = session.agent_state.get_task_for_session(session.id)
            if task_state and task_state.cancelled:
                return "任务已取消"
            return await self.execute_subtask(instance_id, orchestrator, session)
        else:
            # 手动模式：返回，等待用户操作
            return self._format_subtask_complete_result(instance, subtask, output)

    def reset_stale_if_needed(self, instance: BPInstanceSnapshot) -> None:
        """
        如果有 stale 子任务，重置为 PENDING 并将指针移到第一个 stale 位置。
        由 _bp_continue 调用，业务逻辑集中在 engine 而非 handler。

        线性模式：找第一个 stale 索引，将其及后续全部重置。
        DAG 扩展：找 stale 集合中拓扑序最前的节点。
        """
        first_stale_idx = None
        for i, subtask in enumerate(instance.bp_config.subtasks):
            if instance.subtask_statuses.get(subtask.id) == SubtaskStatus.STALE:
                first_stale_idx = i
                break

        if first_stale_idx is not None:
            instance.current_subtask_index = first_stale_idx
            for i, st in enumerate(instance.bp_config.subtasks):
                if i >= first_stale_idx:
                    instance.subtask_statuses[st.id] = SubtaskStatus.PENDING

    def validate_output_soft(
        self,
        instance: BPInstanceSnapshot,
        subtask_id: str,
        output: dict,
    ) -> None:
        """
        宽松校验子任务输出是否符合下游 input_schema。
        不阻断操作，仅记录 warning 供调试。
        """
        config = instance.bp_config
        idx = next((i for i, s in enumerate(config.subtasks) if s.id == subtask_id), None)
        if idx is None or idx >= len(config.subtasks) - 1:
            return  # 最后一个子任务无下游约束

        next_schema = config.subtasks[idx + 1].input_schema
        missing = [k for k in next_schema.get("required", []) if k not in output]
        if missing:
            logger.warning(
                f"[BPEngine] Edited output for '{subtask_id}' missing required fields "
                f"for downstream: {missing}"
            )

    def _resolve_input(
        self,
        instance: BPInstanceSnapshot,
        subtask: SubtaskConfig,
    ) -> dict:
        """
        解析子任务的输入数据。抽象为独立方法，线性/DAG 通用。

        线性模式（当前）：
          - 第一个子任务 → __initial_input__
          - 后续子任务 → 前一个子任务的 output
        DAG 扩展：
          - 按 subtask.input_mapping 从多个上游子任务汇聚输入
          - 无 input_mapping 时退化为线性行为
        """
        config = instance.bp_config
        idx = next(i for i, s in enumerate(config.subtasks) if s.id == subtask.id)

        if subtask.input_mapping:
            # DAG 模式：按映射从多个上游子任务拼装
            return {
                field: instance.subtask_outputs.get(upstream_id, {})
                for field, upstream_id in subtask.input_mapping.items()
            }
        elif idx == 0:
            return instance.subtask_outputs.get("__initial_input__", {})
        else:
            prev_subtask = config.subtasks[idx - 1]
            return instance.subtask_outputs.get(prev_subtask.id, {})

    # ── 输出解析 ──

    def _parse_output_json(self, result: str) -> dict:
        """从 SubAgent 返回的文本中提取 JSON 输出。
        尝试解析 ```json ... ``` 代码块，或整个文本作为 JSON。
        解析失败时返回 {"_raw": result}。"""

    # ── 事件发射 ──

    async def emit_progress(self, instance_id: str, session: "Session") -> None:
        """发射 bp_progress SSE 事件，更新所有 TaskProgressCard。"""

    async def _emit_bp_events(self, instance_id: str, subtask_id: str, output: dict) -> None:
        """发射 bp_progress + bp_subtask_output 事件（子任务完成后调用）。"""

    async def emit_stale(self, instance_id: str, stale_ids: list[str], session: "Session") -> None:
        """发射 bp_stale 事件（Chat-to-Edit 后调用）。"""

    async def emit_subtask_output(
        self, instance_id: str, subtask_id: str, output: dict, session: "Session",
    ) -> None:
        """发射 bp_subtask_output 事件（更新 SubtaskOutputPanel）。"""

    # ── 结果格式化 ──

    def _format_completion_result(self, instance: BPInstanceSnapshot) -> str:
        """格式化 BP 完成结果（所有子任务完成后返回给 MasterAgent 的 tool_result）。"""

    def _format_subtask_complete_result(
        self, instance: BPInstanceSnapshot, subtask: SubtaskConfig, output: dict,
    ) -> str:
        """格式化单个子任务完成结果（手动模式下返回给 MasterAgent，
        引导其使用 ask_user 展示 [查看结果] / [进入下一步]）。"""

    # ── 任务切换 ──

    async def switch_task(
        self,
        target_instance_id: str,
        orchestrator: "AgentOrchestrator",
        session: "Session",
    ) -> str:
        """切换活跃任务：挂起当前 → 恢复目标 → 注入恢复上下文。
        详见 §7.3 ContextBridge。"""
```

### 6.3 对话式编辑 (Chat-to-Edit)

```
用户消息（暂停期间）："把盈利模式改成纯SaaS"
  │
  ▼
MasterAgent 推理：
  系统提示包含 BP_DYNAMIC（输出摘要 + 意图路由指令）
  │
  ▼
LLM 识别：修改子任务 "research" 的输出
  → 调用 bp_edit_output(instance_id, subtask_id="research", changes={...})
  │
  ▼
BPToolHandler.bp_edit_output():
  │
  ├─ 1. state_manager.merge_subtask_output() → deep merge
  │     返回 (old_output, new_output)
  │
  ├─ 2. 检查下游影响：
  │     stale_ids = state_manager.mark_downstream_stale(instance_id, changed_subtask_id)
  │
  ├─ 3. 发射事件：
  │     ├─ bp_subtask_output (更新后的数据)
  │     └─ bp_stale (被标记的子任务列表)
  │
  └─ 4. 返回结果给 MasterAgent：
        ├─ diff 摘要
        ├─ stale 子任务列表
        └─ 如果有 stale → 提示 MasterAgent 用 ask_user 确认是否级联重跑

MasterAgent 收到 tool_result → ask_user 确认级联重跑
  │
  ├─ 用户确认 → 调用 bp_continue (从第一个 stale 子任务开始)
  └─ 用户取消 → 保留修改，下游保持 stale 标记
```

**Deep Merge 实现**（`state_manager.py`）：

```python
def merge_subtask_output(
    self,
    instance_id: str,
    subtask_id: str,
    patch: dict,
) -> tuple[dict, dict]:
    """执行 deep merge，返回 (修改前, 修改后)"""
    instance = self._instances[instance_id]
    old = copy.deepcopy(instance.subtask_outputs.get(subtask_id, {}))
    new = self._deep_merge(old, patch)
    instance.subtask_outputs[subtask_id] = new
    return old, new

@staticmethod
def _deep_merge(base: dict, patch: dict) -> dict:
    """递归合并，patch 中的值覆盖 base"""
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = BPStateManager._deep_merge(result[key], value)
        else:
            result[key] = value
    return result
```

### 6.4 多任务切换

```
用户消息："回到市场调研"
  │
  ▼
MasterAgent 推理：
  系统提示包含 BP_DYNAMIC（所有实例状态表）
  │
  ▼
LLM 识别：切换到实例 "bp-002"
  → 调用 bp_switch_task(target_instance_id="bp-002")
  │
  ▼
BPToolHandler.bp_switch_task():
  │
  ├─ 1. 挂起当前活跃任务
  │     ├─ context_bridge.compress_for_suspend() → context_summary
  │     └─ state_manager.suspend(current_id, context_summary)
  │
  ├─ 2. 恢复目标任务
  │     ├─ state_manager.resume(target_id) → snapshot
  │     └─ context_bridge.prepare_restore_messages(snapshot) → restore_messages
  │
  ├─ 3. 替换 MasterAgent 工作上下文
  │     ├─ 清空 Brain.Context.messages
  │     └─ 注入 restore_messages
  │
  ├─ 4. 发射事件：
  │     └─ bp_task_switch (活跃任务变更)
  │
  └─ 5. 返回结果给 MasterAgent：
        ├─ suspended → "任务已切换。市场调研还剩 2 个子任务..."
        │   MasterAgent → ask_user "是否继续执行？"
        └─ completed → "这个任务已完成。" 附带结果摘要
```

---

## 7. 上下文管理（重点）

### 7.1 三层上下文架构

| 层级 | 数据结构 | 内容 | 生命周期 | BP 中的角色 |
|------|---------|------|---------|-----------|
| **统一时间线** | `SessionContext.messages` | 所有消息（含 metadata: `bp_instance_id`） | 会话级，持久 | 审计回溯，消息归属追踪 |
| **LLM 工作窗口** | `MasterAgent.Brain.Context.messages` | 活跃 BP 对话 + tool_use/result | 任务级，切换时替换 | BP 管理决策的上下文 |
| **子任务上下文** | `SubAgent.Brain.Context.messages` | 委派消息 + 工具调用链 | 子任务级，完成即销毁 | 子任务执行的隔离上下文 |

### 7.2 MasterAgent 上下文的生命周期

```
                      ┌───────────────────────────────────────────┐
                      │        MasterAgent.Brain.Context          │
                      ├───────────────────────────────────────────┤
阶段 1: 无 BP         │ 普通对话消息                               │
                      ├───────────────────────────────────────────┤
阶段 2: BP 启动       │ 普通对话 + bp_start tool_use/result        │
                      ├───────────────────────────────────────────┤
阶段 3: 子任务执行中   │ ... + 委派 tool_use + 委派 tool_result     │
                      │       (SubAgent 结果作为 tool_result)      │
                      ├───────────────────────────────────────────┤
阶段 4: 暂停点        │ ... + SubtaskComplete 消息 + ask_user      │
                      │ 用户可自由对话（Chat-to-Edit / 追问）       │
                      ├───────────────────────────────────────────┤
阶段 5: 任务挂起      │ → ContextManager 压缩 → contextSummary    │
                      │   Context.messages 清空                    │
                      ├───────────────────────────────────────────┤
阶段 6: 恢复其他任务   │ [上下文恢复] + contextSummary              │
                      │ + subtaskOutputs + 新用户消息               │
                      └───────────────────────────────────────────┘
```

### 7.3 上下文切换详细流程 (`context_bridge.py`)

```python
class ContextBridge:
    """管理 BP 任务切换时的上下文压缩与恢复"""

    def __init__(
        self,
        context_manager: "ContextManager",
        prompt_loader: PromptTemplateLoader,
    ) -> None:
        self._context_manager = context_manager
        self._prompt_loader = prompt_loader

    async def compress_for_suspend(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> str:
        """
        将 MasterAgent 当前上下文压缩为摘要文本。

        复用 ContextManager 的 LLM 压缩能力：
        1. 将所有消息交给 ContextManager 压缩
        2. 提取压缩后的摘要文本
        3. 返回纯文本 contextSummary
        """
        # 强制压缩（忽略 soft_limit 阈值）
        compressed = await self._context_manager.compress_if_needed(
            messages,
            system_prompt=system_prompt,
            max_tokens=4096,  # 强制压缩到很小
        )
        # 提取 [之前的对话摘要] 内容
        return self._extract_summary(compressed)

    def prepare_restore_messages(
        self,
        snapshot: BPInstanceSnapshot,
    ) -> list[dict]:
        """
        根据快照生成恢复消息列表，注入 MasterAgent 上下文。

        返回的 messages 结构：
        [
            {"role": "user", "content": "[上下文恢复] ..."},
        ]
        """
        # 构建已完成子任务列表
        completed_lines = []
        for subtask in snapshot.bp_config.subtasks[:snapshot.current_subtask_index]:
            output = snapshot.subtask_outputs.get(subtask.id, {})
            # 恢复时需保留完整 output 以支持 Chat-to-Edit。
            # 仅在 status_table（系统提示注入）中截断为预览。
            output_json = json.dumps(output, ensure_ascii=False, indent=2)
            status = snapshot.subtask_statuses.get(subtask.id, "done")
            completed_lines.append(
                f"- 子任务「{subtask.name}」{status}，输出:\n```json\n{output_json}\n```"
            )

        # 渲染恢复模板
        restore_text = self._prompt_loader.render(
            "context_restore",
            bp_name=snapshot.bp_config.name,
            completed_subtasks="\n".join(completed_lines),
            next_index=snapshot.current_subtask_index + 1,
            total=len(snapshot.bp_config.subtasks),
            run_mode="手动" if snapshot.run_mode == RunMode.MANUAL else "自动",
            context_summary=snapshot.context_summary,
        )

        return [{"role": "user", "content": restore_text}]
```

### 7.4 SubAgent 上下文隔离

SubAgent 的上下文与 MasterAgent **完全隔离**（已有机制）：

```
AgentOrchestrator.delegate()
  │
  ├─ message = rendered subtask_instruction (单条消息)
  │
  ├─ SubAgent.chat_with_session(
  │     message=message,        ← 仅此一条消息
  │     session_messages=[],     ← 不传历史
  │     session=session,         ← 共享 Session 引用（用于事件总线）
  │   )
  │
  ├─ SubAgent 内部 ReAct 循环：
  │   Brain.Context.messages = [
  │     {"role": "user", "content": "[委派任务] ...subtask_instruction..."},
  │     {"role": "assistant", "content": [tool_use: web_search]},
  │     {"role": "user", "content": [tool_result: ...]},
  │     {"role": "assistant", "content": [tool_use: read_file]},
  │     {"role": "user", "content": [tool_result: ...]},
  │     {"role": "assistant", "content": "最终输出：{...}"},
  │   ]
  │
  └─ 返回最终输出文本 → MasterAgent 收到作为 tool_result
     SubAgent.Brain.Context → 销毁（GC 回收）
```

**要点**：
- SubAgent 不知道自己在 BP 流程中（对它来说就是一个普通委派任务）
- SubAgent 的 SSE 事件通过 `session.context._sse_event_bus` 流到前端
- SubAgent 的工具/技能由 `AgentProfile` 的 `skills_mode` / `tools_mode` / `mcps_mode` 控制

### 7.5 消息标签方案

利用 `SessionContext.add_message()` 的 `**metadata` 机制，为 BP 相关消息打标签：

```python
# 在 BPToolHandler 执行前后，通过 session 记录消息
session.context.add_message(
    role="assistant",
    content="开始执行子任务「市场调研」...",
    bp_instance_id="bp-001",
    subtask_id="research",
    bp_event="subtask_start",
)
```

这使得统一时间线中的消息可按 `bp_instance_id` 过滤：

```python
# 获取某个 BP 实例的所有消息
bp_messages = [
    m for m in session.context.messages
    if m.get("bp_instance_id") == "bp-001"
]
```

---

## 8. 多 Agent 协作

### 8.1 角色分工

```
┌───────────────────────────────────────────────────────────┐
│  MasterAgent（主 Agent）                                    │
│  职责：对话管理、意图识别、BP 工具调用、用户交互              │
│  上下文：完整用户对话 + BP 管理消息                          │
│  工具：bp_start, bp_continue, bp_edit_output,              │
│        bp_switch_task + 所有现有工具                        │
├───────────────────────────────────────────────────────────┤
│  BPEngine（编排引擎 — 非 Agent，纯逻辑）                     │
│  职责：实例管理、子任务调度、Schema 推导、事件发射            │
│  不参与 LLM 推理，是确定性代码                               │
├───────────────────────────────────────────────────────────┤
│  SubAgent-N（子任务 Agent）                                  │
│  职责：执行具体子任务（调研、分析、报告等）                   │
│  上下文：隔离（仅委派消息 + 自身工具调用）                   │
│  工具/技能：由 AgentProfile 控制（per-agent 过滤）           │
└───────────────────────────────────────────────────────────┘
```

### 8.2 通信协议

| 通信路径 | 机制 | 数据格式 |
|---------|------|---------|
| 用户 → MasterAgent | `chat_with_session_stream()` 入参 | 自然语言文本 |
| MasterAgent → BPEngine | 工具调用 `bp_*` | `tool_use` 参数 JSON |
| BPEngine → MasterAgent | 工具返回值 | `tool_result` 文本（含 JSON） |
| BPEngine → SubAgent | `orchestrator.delegate()` | 渲染后的委派消息文本 |
| SubAgent → BPEngine | `delegate()` 返回值 | 结果文本（含 output JSON） |
| BPEngine → 前端 | `session.context._sse_event_bus` | `bp_*` 事件 dict |
| MasterAgent → 前端 | `reason_stream()` → SSE | 标准 SSE 事件流 |

### 8.3 事件流序列图

以手动模式执行 3 个子任务为例：

```
  User          MasterAgent        BPEngine       SubAgent-1     SubAgent-2     Frontend
   │                │                 │               │              │             │
   │─"做市场调研"──▶│                 │               │              │             │
   │                │──ask_user──────▶│               │              │             │
   │                │                 │               │              │         ◀──ask_user
   │─"最佳实践"───▶│                 │               │              │             │
   │                │──bp_start──────▶│               │              │             │
   │                │                 │──bp_progress─────────────────────────▶ TaskProgressCard
   │                │                 │──delegate───▶│              │             │
   │                │                 │              │──thinking──────────────▶ ThinkingBlock
   │                │                 │              │──tool_call_start────────▶ StepCard
   │                │                 │              │──tool_call_end──────────▶ StepCard✅
   │                │                 │              │──text_delta────────────▶ AI文本
   │                │                 │◀──result─────│              │             │
   │                │                 │──bp_progress─────────────────────────▶ 进度更新
   │                │                 │──bp_subtask_output───────────────────▶ OutputPanel
   │                │◀──tool_result───│               │              │             │
   │                │──ask_user("查看结果/下一步")────────────────────────────▶ 完成块
   │                │                 │               │              │             │
   │─"进入下一步"─▶│                 │               │              │             │
   │                │──bp_continue──▶│               │              │             │
   │                │                 │──bp_progress─────────────────────────▶ 进度更新
   │                │                 │──delegate────────────────▶│             │
   │                │                 │              │             │──thinking──▶
   │                │                 │              │             │──steps────▶
   │                │                 │◀─────────────────result───│             │
   │                │                 │──bp_subtask_output───────────────────▶ OutputPanel
   │                │◀──tool_result───│               │              │             │
   │                │──完成消息─────────────────────────────────────────────▶ AI文本
```

---

## 9. SSE 事件集成

### 9.1 新增事件定义

```python
# 所有 BP 事件通过 session.context._sse_event_bus 发射
# SeeCrabAdapter 对未知 type 透传，前端 useBestPracticeStore 处理

# ── bp_progress: 进度更新 ──
{
    "type": "bp_progress",
    "instance_id": "bp-001",
    "bp_name": "市场调研报告",
    "subtasks": [
        {"id": "research", "name": "市场调研"},
        {"id": "analysis", "name": "数据分析"},
        {"id": "report", "name": "报告生成"},
    ],
    "statuses": {                              # 主真值源（与 BPInstanceSnapshot.subtask_statuses 一致）
        "research": "done",
        "analysis": "current",
        "report": "pending",
    },
    "run_mode": "manual",
}

# ── bp_subtask_output: 子任务输出数据 ──
{
    "type": "bp_subtask_output",
    "instance_id": "bp-001",
    "subtask_id": "research",
    "subtask_name": "市场调研",
    "output": { ... },                     # 完整输出 JSON
    "output_schema": { ... },              # 下游 input_schema（供前端渲染表单）
}

# ── bp_stale: 子任务标记过期 ──
{
    "type": "bp_stale",
    "instance_id": "bp-001",
    "stale_subtask_ids": ["analysis", "report"],
    "reason": "上游子任务 research 的输出已修改",
}

# ── bp_task_switch: 任务切换 ──
{
    "type": "bp_task_switch",
    "session_id": "...",
    "suspended_instance_id": "bp-001",     # null if no previous active
    "activated_instance_id": "bp-002",
    "activated_bp_name": "竞品技术分析",
    "activated_status": "suspended",       # 恢复前的状态
}
```

### 9.2 事件发射实现

```python
# engine.py
class BPEngine:
    async def _emit_event(
        self,
        event_type: str,
        data: dict,
        session: "Session",
    ) -> None:
        """通过 session 的 SSE 事件总线发射 BP 事件"""
        event_bus = getattr(session.context, "_sse_event_bus", None)
        if event_bus is not None:
            await event_bus.put({"type": event_type, **data})
```

### 9.3 与现有 SSE 管道的集成

```
SubAgent 原始事件 ──┐
                    ├──→ _sse_event_bus ──→ SeeCrabAdapter.transform()
BP 事件 ───────────┘                           │
                                               ├─ 已知类型 → 正常处理 (step_card, thinking, etc.)
                                               └─ bp_* 类型 → 透传到前端
                                                     │
                                                     ▼
                                               SSE StreamingResponse
                                                     │
                                                     ▼
                                               前端 SSEClient
                                                     │
                                    ┌────────────────┼────────────────┐
                                    ▼                ▼                ▼
                             useChatStore      useBestPractice   useUIStore
                            (step_card等)     Store(bp_*事件)   (面板控制)
```

---

## 10. 工具定义

### 10.1 BP 工具注册

BP 工具注册在 `tools/handlers/bestpractice.py`，通过 `ToolCatalog` 的标准机制加入系统。
采用**渐进式披露**（Level 1 目录 → Level 2 详情），不加入 HIGH_FREQ_TOOLS。

```python
BP_TOOL_DEFINITIONS = [
    {
        "name": "bp_start",
        "description": "启动最佳实践任务执行",
        "input_schema": {
            "type": "object",
            "properties": {
                "bp_id": {"type": "string", "description": "最佳实践配置 ID"},
                "input_data": {"type": "object", "description": "初始输入数据"},
            },
            "required": ["bp_id", "input_data"],
        },
        "category": "BestPractice",
        "triggers": ["用户确认进入最佳实践模式时调用"],
    },
    {
        "name": "bp_continue",
        "description": "继续执行下一个子任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
            },
            "required": ["instance_id"],
        },
        "category": "BestPractice",
    },
    {
        "name": "bp_edit_output",
        "description": "编辑已完成子任务的输出（Deep Merge）",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
                "subtask_id": {"type": "string"},
                "changes": {"type": "object", "description": "要修改的字段及新值"},
            },
            "required": ["instance_id", "subtask_id", "changes"],
        },
        "category": "BestPractice",
    },
    {
        "name": "bp_switch_task",
        "description": "切换到另一个最佳实践实例",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_instance_id": {"type": "string"},
            },
            "required": ["target_instance_id"],
        },
        "category": "BestPractice",
    },
]
```

### 10.2 BPToolHandler

> **关键约束**：`SystemHandlerRegistry` 的 handler 签名固定为 `(tool_name: str, params: dict) -> str`
> （见 `HandlerFunc = Callable[[str, dict], str | Awaitable[str]]`），
> 无法在调用时注入额外参数。因此 BPToolHandler 采用与 `AgentToolHandler` 相同的模式：
> 构造时存储 `agent` 引用，运行时通过 `agent._current_session` 获取 session，
> 通过模块级 `_orchestrator` 单例获取 orchestrator。

```python
# tools/handlers/bestpractice.py

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class BPToolHandler:
    """
    BP 工具的薄层处理器。
    不包含业务逻辑，仅负责参数解析和调用 BPEngine。

    与 AgentToolHandler 同级注册在 SystemHandlerRegistry 中。

    获取运行时上下文的方式（与 AgentToolHandler 一致）：
    - session: self._agent._current_session （Agent 在 _prepare_session_context 中设置）
    - orchestrator: seeagent.main._orchestrator （模块级单例）
    """

    TOOLS = ["bp_start", "bp_continue", "bp_edit_output", "bp_switch_task"]

    def __init__(self, agent: Agent) -> None:
        self._agent = agent
        # engine 和 state_manager 延迟获取，避免循环导入
        self._engine: BPEngine | None = None
        self._state: BPStateManager | None = None

    def _ensure_deps(self) -> None:
        """延迟初始化 BPEngine 和 BPStateManager（首次调用时创建）。
        使用模块级单例，确保与 PromptAssembler 共享同一个 BPEngine。"""
        if self._engine is None:
            from ...bestpractice import get_bp_engine
            self._engine = get_bp_engine()  # 模块级单例工厂
            self._state = self._engine._state_manager

    def _get_session(self):
        """获取当前活跃 session（与 AgentToolHandler 相同模式）。"""
        session = getattr(self._agent, "_current_session", None)
        if session is None:
            raise RuntimeError("No active session — BP tools require a session context")
        return session

    def _get_orchestrator(self):
        """获取 orchestrator 单例（与 AgentToolHandler._get_orchestrator 相同模式）。"""
        try:
            import seeagent.main as _main_mod
            orch = _main_mod._orchestrator
            if orch is None:
                logger.warning("[BPToolHandler] _orchestrator is None")
            return orch
        except (ImportError, AttributeError) as e:
            logger.warning(f"[BPToolHandler] Cannot access _orchestrator: {e}")
            return None

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """
        路由工具调用到对应方法。

        签名严格匹配 HandlerFunc = Callable[[str, dict], str | Awaitable[str]]，
        session/orchestrator 通过 self._agent 和模块级单例在内部获取。
        """
        self._ensure_deps()

        session = self._get_session()
        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "❌ Orchestrator not available — multi-agent mode may not be initialised"

        handler = {
            "bp_start": self._bp_start,
            "bp_continue": self._bp_continue,
            "bp_edit_output": self._bp_edit_output,
            "bp_switch_task": self._bp_switch_task,
        }.get(tool_name)
        if handler is None:
            return f"未知的 BP 工具: {tool_name}"
        return await handler(params, session=session, orchestrator=orchestrator)

    async def _bp_start(self, args: dict, *, session, orchestrator) -> str:
        config = self._engine.get_config(args["bp_id"])
        if config is None:
            return f"未找到最佳实践配置: {args['bp_id']}"

        instance = self._state.create_instance(config, session.id)
        self._state.update_subtask_output(
            instance.instance_id, "__initial_input__", args.get("input_data", {})
        )

        # 发射初始进度事件
        await self._engine.emit_progress(instance.instance_id, session)

        # 执行第一个子任务
        return await self._engine.execute_subtask(
            instance.instance_id, orchestrator, session,
        )

    async def _bp_continue(self, args: dict, *, session, orchestrator) -> str:
        instance_id = args["instance_id"]
        instance = self._state.get(instance_id)
        if instance is None:
            return "实例不存在"

        # stale 重置逻辑委托给 engine（薄层不含业务逻辑）
        self._engine.reset_stale_if_needed(instance)

        return await self._engine.execute_subtask(
            instance_id, orchestrator, session,
        )

    async def _bp_edit_output(self, args: dict, *, session, orchestrator) -> str:
        old, new = self._state.merge_subtask_output(
            args["instance_id"], args["subtask_id"], args["changes"],
        )

        # 宽松校验：合并后的 output 仍应满足下游子任务的 input_schema
        # 只记录 warning，不阻断编辑（用户意图优先）
        instance = self._state.get(args["instance_id"])
        if instance:
            self._engine.validate_output_soft(instance, args["subtask_id"], new)

        # 标记下游 stale（基于 subtask_id，线性/DAG 通用）
        stale_ids = self._state.mark_downstream_stale(
            args["instance_id"], args["subtask_id"],
        )

        # 发射事件
        await self._engine.emit_stale(args["instance_id"], stale_ids, session)
        await self._engine.emit_subtask_output(
            args["instance_id"], args["subtask_id"], new, session,
        )

        if stale_ids:
            return (
                f"已修改子任务「{args['subtask_id']}」的输出。"
                f"下游子任务 {stale_ids} 需基于新数据重新执行。"
                f"请使用 ask_user 确认用户是否继续级联重跑。"
            )
        return f"已修改子任务「{args['subtask_id']}」的输出，无需重跑下游。"

    async def _bp_switch_task(self, args: dict, *, session, orchestrator) -> str:
        return await self._engine.switch_task(
            target_instance_id=args["target_instance_id"],
            orchestrator=orchestrator,
            session=session,
        )
```

---

## 11. Schema 推导链 (`schema_chain.py`)

```python
class SchemaChain:
    """
    子任务间的 Schema 推导逻辑。

    线性模式：output_schema = 下一个子任务的 input_schema。
    DAG 扩展：每个子任务显式声明 input_schema，output_schema 由
             下游子任务的 input_schema 集合推导（或使用 final_output_schema）。
    """

    @staticmethod
    def derive_output_schema(
        config: BestPracticeConfig,
        current_index: int,
    ) -> dict | None:
        """
        推导当前子任务的输出 Schema。

        线性模式规则：
        - 非最后一个子任务 → 输出 = 下一个子任务的 input_schema
        - 最后一个子任务 → 输出 = config.final_output_schema（可选）

        DAG 扩展提示：改为接受 subtask_id，
        查找所有 depends_on 包含该 subtask_id 的下游子任务，
        合并其 input_schema 作为输出约束。
        """
        if current_index < len(config.subtasks) - 1:
            return config.subtasks[current_index + 1].input_schema
        return config.final_output_schema  # 可能为 None

    @staticmethod
    def validate_input(
        schema: dict,
        data: dict,
    ) -> list[str]:
        """
        宽松验证输入数据是否满足 Schema。
        返回缺失的 required 字段列表（空列表表示验证通过）。
        不使用 jsonschema 库，仅检查 required 字段存在性。
        """
        required = schema.get("required", [])
        if isinstance(required, list):
            return [f for f in required if f not in data]
        # 兼容 property-level required
        props = schema.get("properties", {})
        return [
            k for k, v in props.items()
            if v.get("required") is True and k not in data
        ]
```

---

## 12. 与 PromptAssembler 的集成

### 12.1 集成接口

```python
# engine.py
class BPEngine:
    def get_static_prompt_section(self) -> str:
        """
        返回 BP 静态提示段，注入到系统提示的可缓存区域。
        内容包含：BP 能力说明 + 可用 BP 列表 + 交互规则。
        会话内稳定（除非动态添加新 BP 配置）。
        """
        practices_text = self._format_available_practices()
        return self._prompt_loader.render(
            "system_static",
            available_practices=practices_text,
        )

    def get_dynamic_prompt_section(self, session_id: str) -> str:
        """
        返回 BP 动态提示段，注入到系统提示的动态区域。
        内容包含：当前实例状态表 + 活跃任务上下文。
        每次 LLM 调用都可能不同。
        """
        status_table = self._state_manager.get_status_table(session_id)
        active = self._state_manager.get_active(session_id)

        if active is None:
            return ""

        active_context = self._state_manager.get_outputs_summary(active.instance_id)
        return self._prompt_loader.render(
            "system_dynamic",
            status_table=status_table,
            active_context=active_context,
        )
```

### 12.2 PromptAssembler 修改（最小化）

```python
# prompt_assembler.py — 修改点：build_system_prompt 增加 session_id 参数，
# bp_engine 通过 get_bp_engine() 延迟获取（与 BPToolHandler 共享同一单例）

class PromptAssembler:
    def __init__(self, ...):
        ...
        # 不在构造时注入 bp_engine，避免启动时加载 bestpractice 包
        self._bp_engine: "BPEngine | None" = None

    def _get_bp_engine(self) -> "BPEngine | None":
        """延迟获取 BPEngine 单例（与 BPToolHandler 共享）。"""
        if self._bp_engine is None:
            try:
                from ..bestpractice import get_bp_engine
                self._bp_engine = get_bp_engine()
            except ImportError:
                pass  # bestpractice 模块未安装
        return self._bp_engine

    def build_system_prompt(
        self,
        base_prompt: str,
        tools: list[dict],
        *,
        task_description: str = "",
        use_compiled: bool = False,
        session_type: str = "cli",
        skill_catalog_text: str = "",
        session_id: str = "",              # 新增：BP 动态段需要 session_id 查询活跃实例
    ) -> str:
        ...
        bp_engine = self._get_bp_engine()

        # 新增：BP 静态段（可缓存）
        bp_static = bp_engine.get_static_prompt_section() if bp_engine else ""

        # 新增：BP 动态段（需要 session_id）
        bp_dynamic = (
            bp_engine.get_dynamic_prompt_section(session_id)
            if bp_engine and session_id else ""
        )

        return f"""{base_prompt}

{system_info}
{env_snapshot}
{skill_catalog}
{mcp_catalog}
{memory_context}

{tools_text}

{tools_guide}

{core_principles}
{bp_static}
{bp_dynamic}
{profile_prompt}"""
```

---

## 13. 触发检测 (`trigger.py`)

```python
class BPTriggerDetector:
    """检测用户消息是否匹配 BestPractice 触发条件"""

    def __init__(self, configs: dict[str, BestPracticeConfig]) -> None:
        self._configs = configs
        self._cooldowns: dict[str, int] = {}  # session_id → remaining_turns

    def detect(
        self,
        message: str,
        session_id: str,
    ) -> list[TriggerMatch]:
        """
        扫描所有配置的触发条件，返回匹配结果列表。

        优先级：COMMAND > CONTEXT（有 cooldown 限制）
        EVENT 和 UI_CLICK 不通过此方法触发。
        """
        if self._cooldowns.get(session_id, 0) > 0:
            return []  # 冷却期内不检测 CONTEXT

        matches = []
        for config in self._configs.values():
            for trigger in config.triggers:
                if trigger.type == "command" and self._match_command(message, trigger):
                    matches.append(TriggerMatch(config, trigger, confidence=1.0))
                elif trigger.type == "context" and self._match_context(message, trigger):
                    matches.append(TriggerMatch(config, trigger, confidence=0.7))

        return sorted(matches, key=lambda m: m.confidence, reverse=True)

    def apply_cooldown(self, session_id: str, turns: int = 5) -> None:
        """选择自由模式后设置冷却"""
        self._cooldowns[session_id] = turns

    def tick_cooldown(self, session_id: str) -> None:
        """每轮用户输入递减冷却计数"""
        if session_id in self._cooldowns:
            self._cooldowns[session_id] = max(0, self._cooldowns[session_id] - 1)


@dataclass
class TriggerMatch:
    config: BestPracticeConfig
    trigger: TriggerConfig
    confidence: float
```

> **注意**：CONTEXT 触发的最终决策由 LLM 在 MasterAgent 推理中完成（系统提示包含可用 BP 列表和触发条件），`TriggerDetector` 仅做预筛选。COMMAND 触发由 LLM 直接识别（高置信度）。

---

## 14. 前端集成要点

### 14.1 Pinia Store (`useBestPracticeStore`)

```typescript
interface BestPracticeState {
  instances: Map<string, BPInstanceClient>;
  activeInstanceId: string | null;
}

interface BPInstanceClient {
  instanceId: string;
  bpName: string;
  subtasks: { id: string; name: string }[];
  currentIndex: number;
  statuses: Record<string, 'pending' | 'current' | 'done' | 'stale'>;
  runMode: 'manual' | 'auto';
  outputs: Record<string, any>;
  status: 'active' | 'suspended' | 'completed';
}

// Actions
function handleBPEvent(event: SSEEvent): void {
  switch (event.type) {
    case 'bp_progress':
      // 更新所有 TaskProgressCard
      break;
    case 'bp_subtask_output':
      // 更新 SubtaskOutputPanel 数据
      break;
    case 'bp_stale':
      // 标记 stale 样式
      break;
    case 'bp_task_switch':
      // 切换活跃实例
      break;
  }
}
```

### 14.2 组件映射

| SSE 事件 | Pinia Action | 影响的组件 |
|---------|-------------|----------|
| `bp_progress` | `updateProgress()` | 所有 `TaskProgressCard` 实例同步刷新 |
| `bp_subtask_output` | `setOutput()` | `SubtaskOutputPanel` |
| `bp_stale` | `markStale()` | `TaskProgressCard`（黄色虚线）+ `SubtaskOutputPanel`（待重跑标记）|
| `bp_task_switch` | `switchActive()` | 所有卡片 + 面板 |

---

## 15. 核心设计决策总结

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| BP 与 Agent 的关系 | A) BP 作为 Agent / B) BP 作为工具 / C) BP 作为中间件 | **B) 工具** | 低耦合，复用现有工具基础设施，MasterAgent 自然管理对话 |
| 子任务执行粒度 | A) 整个 BP 一次工具调用 / B) 每个子任务一次 | **B) 每个子任务一次** | 允许子任务间用户交互（Chat-to-Edit），支持手动模式暂停 |
| 自动模式实现 | A) MasterAgent 决定继续 / B) 工具内递归 | **B) 工具内递归** | 避免额外 LLM 轮次，降低延迟和成本 |
| 状态存储 | A) 扩展 AgentState / B) 扩展 SessionContext / C) 独立 | **C) 独立 BPStateManager** | 低耦合，清晰职责边界，可独立演进 |
| Prompt 注入方式 | A) 硬编码 / B) 模板文件 | **B) 模板文件** | 可维护性，非工程师也可调整 prompt |
| 上下文切换 | A) 追加标记 / B) 清空替换 | **B) 清空替换** | 避免上下文膨胀，干净的任务隔离 |
| 触发检测 | A) 纯 LLM / B) 规则预筛 + LLM 确认 | **B) 规则预筛 + LLM** | 降低误触发率，减少不必要的 LLM 调用 |
| Prompt KV-Cache | A) 不考虑 / B) 静态段前置 | **B) 静态段前置** | BP 规则和可用列表会话内不变，适合缓存 |
| 子任务调度模型 | A) 固定线性 / B) 线性优先，预留 DAG 扩展 | **B) 线性 + DAG 预留** | 当前需求为线性，但 `depends_on` / `input_mapping` / `subtask_statuses` 作为主真值源等设计使 DAG 扩展零破坏性 |

---

## 附录 A：DAG 调度扩展设计（预留）

当前 BP 子任务按 `subtasks` 列表顺序线性执行。以下记录在不改变核心接口的前提下，扩展为 DAG（有向无环图）调度的设计方案，供后续需求使用。

### A.1 线性模型是 DAG 的退化形式

```yaml
# 线性（当前，隐式 depends_on）
subtasks:
  - id: research       # depends_on: []（首任务）
  - id: analyze        # 隐式 depends_on: [research]
  - id: report         # 隐式 depends_on: [analyze]

# DAG（扩展后，显式 depends_on）
subtasks:
  - id: research_market
  - id: research_tech
  - id: merge_analysis
    depends_on: [research_market, research_tech]   # fan-in
  - id: review_legal
    depends_on: [merge_analysis]
  - id: review_security
    depends_on: [merge_analysis]                   # fan-out
  - id: final_report
    depends_on: [review_legal, review_security]    # fan-in
```

**兼容性**：`depends_on` 为空列表时，按 `subtasks` 列表索引隐式推导线性依赖。现有 YAML 配置无需修改。

### A.2 需要变更的模块

| 模块 | 当前（线性） | DAG 扩展后 | 改动量 |
|------|-------------|-----------|--------|
| `config.py` SubtaskConfig | `depends_on`/`input_mapping` 字段已预留 | 填充实际值 | 无代码改动，仅配置 |
| `state_manager.py` | `subtask_statuses` 已是主真值源 | 新增 `get_ready_subtasks()` 方法 | 小 |
| `state_manager.py` mark_downstream_stale | 线性：index 之后全标 | BFS 遍历依赖图 | 小（下方有伪代码） |
| `engine.py` execute_subtask | 单任务执行 | 新增 `execute_next()`：并行 gather ready 集合 | 中 |
| `engine.py` _resolve_input | 已支持 `input_mapping` 分支 | 无需改动 | 零 |
| `schema_chain.py` | `next_subtask.input_schema` | 每个子任务显式声明 `input_schema` | 小 |
| `AgentOrchestrator` | 不变 | 不变 | 零 |
| `BPToolHandler` | `bp_continue` 语义不变 | 内部调用 `execute_next()` 而非 `execute_subtask()` | 极小 |

### A.3 关键扩展代码示意

```python
# state_manager.py — 新增方法

def get_ready_subtasks(self, instance_id: str) -> set[str]:
    """返回所有依赖已满足、且未完成/未运行的子任务 ID"""
    instance = self._instances[instance_id]
    completed = {
        sid for sid, st in instance.subtask_statuses.items()
        if st == SubtaskStatus.DONE
    }
    running = {
        sid for sid, st in instance.subtask_statuses.items()
        if st == SubtaskStatus.CURRENT
    }
    ready = set()
    for subtask in instance.bp_config.subtasks:
        if subtask.id in completed or subtask.id in running:
            continue
        deps = subtask.depends_on or self._infer_linear_dep(instance.bp_config, subtask.id)
        if set(deps).issubset(completed):
            ready.add(subtask.id)
    return ready

def mark_downstream_stale(self, instance_id: str, changed_subtask_id: str) -> list[str]:
    """BFS 遍历依赖图，标记所有可达下游节点为 stale"""
    instance = self._instances[instance_id]
    # 构建下游邻接表
    downstream: dict[str, set[str]] = defaultdict(set)
    for s in instance.bp_config.subtasks:
        deps = s.depends_on or self._infer_linear_dep(instance.bp_config, s.id)
        for dep in deps:
            downstream[dep].add(s.id)
    # BFS
    stale = []
    queue = deque([changed_subtask_id])
    visited = {changed_subtask_id}
    while queue:
        current = queue.popleft()
        for child in downstream.get(current, set()):
            if child not in visited:
                visited.add(child)
                if instance.subtask_statuses.get(child) == SubtaskStatus.DONE:
                    instance.subtask_statuses[child] = SubtaskStatus.STALE
                    stale.append(child)
                queue.append(child)
    return stale


# engine.py — DAG 调度入口

async def execute_next(
    self,
    instance_id: str,
    orchestrator: "AgentOrchestrator",
    session: "Session",
) -> str:
    """执行所有 ready 子任务（可并行），替代线性模式的 execute_subtask"""
    ready = self._state_manager.get_ready_subtasks(instance_id)

    if not ready:
        instance = self._state_manager.get(instance_id)
        running = {
            sid for sid, st in instance.subtask_statuses.items()
            if st == SubtaskStatus.CURRENT
        }
        if running:
            return "等待执行中的子任务完成..."
        self._state_manager.complete(instance_id)
        return self._format_completion_result(instance)

    if len(ready) == 1:
        return await self.execute_subtask_by_id(
            instance_id, ready.pop(), orchestrator, session,
        )

    # 多任务并行 — 复用 AgentOrchestrator 的并发能力
    results = await asyncio.gather(*[
        self.execute_subtask_by_id(instance_id, sid, orchestrator, session)
        for sid in ready
    ])
    return "\n---\n".join(results)
```

### A.4 不需要改动的模块

以下模块在 DAG 扩展中**零改动**，说明当前设计的扩展点选择正确：

- **`AgentOrchestrator`** — `delegate()` 是无状态的「给某个 agent 执行一个任务」，不关心调用者是线性链还是 DAG
- **`AgentToolHandler`** — 与 BP 模块完全解耦
- **`ContextBridge`** — 压缩/恢复逻辑不依赖子任务拓扑
- **`PromptTemplateLoader`** — 模板渲染与调度模型无关
- **`BPToolHandler`** — 薄层，内部调用 engine 方法名可能变化，但接口（`handle(tool_name, args)`）不变
- **SSE 事件定义** — `bp_progress` 已用 `statuses: dict` 而非 `current_index` 传递进度，天然支持多任务并行状态
