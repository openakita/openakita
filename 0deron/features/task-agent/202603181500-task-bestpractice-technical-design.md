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
├── context_bridge.py         # 上下文桥接（压缩/恢复/注入）
├── prompt_loader.py          # Prompt 模板加载器
└── prompts/                  # Prompt 模板文件
    ├── system_static.md      # 系统提示 — 静态段（可缓存）
    ├── system_dynamic.md     # 系统提示 — 动态段（每次请求，含条件性意图路由指令）
    ├── subtask_instruction.md # SubAgent 子任务指令
    ├── chat_to_edit.md       # Deep Merge 指令
    ├── context_restore.md    # 上下文恢复注入
    └── cascade_confirm.md    # 级联重跑确认

src/seeagent/tools/handlers/
└── bestpractice.py           # BPToolHandler — 工具处理器（薄层）
```

#### BP 配置文件目录（项目根目录 / 用户自定义）

```
best_practice/                       # 用户自定义 BP 配置根目录（注意下划线）
├── _shared/                         # 可复用的共享 Agent（所有 BP 可引用）
│   ├── profiles/
│   │   └── web-researcher.json
│   └── prompts/
│       └── web-researcher.md
├── {bp-id}/                         # 每个 BP 一个子目录，bp-id 与 config.yaml 中的 id 一致
│   ├── config.yaml                  # BP 配置文件（字段直接对应 BestPracticeConfig，无包装键）
│   ├── profiles/                    # 该 BP 使用的 Agent Profile
│   │   └── {agent-id}.json         # AgentProfile JSON（含 prompt_file 指向 prompts/ 下的 .md）
│   └── prompts/                     # Agent 角色 Prompt（独立 .md 文件，可维护性好）
│       └── {agent-id}.md
```

**配置文件格式**：`config.yaml` 顶层键直接对应 `BestPracticeConfig` 字段（`id`、`name`、`subtasks` 等），不使用 `best_practice:` 包装键。

**Agent Profile JSON**：包含标准 `AgentProfile` 字段，额外有 `prompt_file` 字段指向相对路径的 `.md` 文件。`BPConfigLoader` 在加载时读取 `.md` 内容填入 `custom_prompt`，注册到 `ProfileStore`。

**`_shared/` 目录**：共享 Agent 先于各 BP 子目录加载，可被多个 BP 的 `subtask.agent_profile` 引用。

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
SystemHandlerRegistry (已有)
    └── tools/handlers/bestpractice.py ──→ engine.py + state_manager.py
```

- `engine.py` 是唯一的 "胖" 模块，依赖多个内部模块 + 外部 `AgentOrchestrator`
- `BPToolHandler` 在 `SystemHandlerRegistry` 中注册，与 `AgentToolHandler` 并列
- 触发检测通过系统提示引导 LLM 直接识别，无需独立模块（M4 改进）

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
    CANCELLED = "cancelled"                         # H5 改进：用户主动取消


class SubtaskStatus(str, Enum):
    PENDING = "pending"
    CURRENT = "current"
    DONE = "done"
    STALE = "stale"
    FAILED = "failed"                               # H7 改进：区分"未执行"和"执行失败"


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
    timeout_seconds: int | None = None          # 子任务超时（秒），None 使用全局默认值（M9）
    max_retries: int = 0                        # 最大重试次数，0 表示不重试（M9）


@dataclass
class PendingContextSwitch:
    """待执行的上下文切换操作，由 bp_switch_task 创建，由 Agent 推理准备阶段消费（C2）"""
    suspended_instance_id: str      # 要挂起的实例
    target_instance_id: str         # 要恢复的实例
    created_at: float = 0.0        # 创建时间


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
    initial_input: dict = field(default_factory=dict)
    # bp_start 时传入的初始输入数据（M8 改进，替代 subtask_outputs["__initial_input__"] 魔法键）
    subtask_outputs: dict[str, dict] = field(default_factory=dict)
    # subtask_id → output JSON

    # 上下文
    context_summary: str = ""                   # 挂起时 LLM 压缩生成

    # 配置引用
    bp_config: BestPracticeConfig | None = None
    # 实际使用中，bp_config 仅在反序列化恢复的瞬间为 None，
    # 恢复完成后由 BPEngine.restore_session 关联。
    # 所有正常路径中 bp_config 非空。（D3 改进）
```

### 3.3 配置加载 (`config.py`)

```python
class BPConfigLoader:
    """从子目录结构发现并加载 BestPractice 配置及 Agent Profile。

    目录结构约定：
    best_practice/
    ├── _shared/profiles/*.json       # 共享 Agent（先加载）
    ├── {bp-id}/config.yaml           # BP 配置（字段直接对应 BestPracticeConfig）
    ├── {bp-id}/profiles/*.json       # BP 专用 Agent Profile
    └── {bp-id}/prompts/*.md          # Agent 角色 Prompt
    """

    def __init__(
        self,
        search_paths: list[Path],
        profile_store: "ProfileStore | None" = None,
    ):
        # 默认搜索路径：
        # 1. src/seeagent/bestpractice/configs/  （系统内置）
        # 2. {project_root}/best_practice/         （用户自定义，注意下划线）
        self._search_paths = search_paths
        self._profile_store = profile_store
        self._configs: dict[str, BestPracticeConfig] = {}

    def load_all(self) -> dict[str, BestPracticeConfig]:
        """扫描所有路径的子目录，解析 config.yaml → BestPracticeConfig。
        同时加载 Agent Profile 并注册到 ProfileStore。
        对每个配置进行验证，记录 warning 但不阻断加载（M12 改进）。"""
        configs = {}
        for path in self._search_paths:
            if not path.is_dir():
                continue

            # 1. 优先加载 _shared/ 目录的共享 Agent Profile
            shared_dir = path / "_shared"
            if shared_dir.is_dir():
                self._load_profiles_from_dir(shared_dir)

            # 2. 遍历子目录，查找 config.yaml
            for bp_dir in sorted(path.iterdir()):
                if not bp_dir.is_dir() or bp_dir.name.startswith(("_", ".")):
                    continue
                config_file = bp_dir / "config.yaml"
                if not config_file.exists():
                    continue
                try:
                    config = self._parse_yaml(config_file)
                    errors = self._validate(config)
                    if errors:
                        logger.warning(
                            f"BP config '{config_file}' has validation warnings:\n"
                            + "\n".join(f"  - {e}" for e in errors)
                        )
                    configs[config.id] = config

                    # 3. 加载该 BP 目录下的 Agent Profile
                    self._load_profiles_from_dir(bp_dir)
                except Exception as e:
                    logger.error(f"Failed to load BP config '{config_file}': {e}")

        self._configs = configs
        return configs

    def _parse_yaml(self, yaml_file: Path) -> BestPracticeConfig:
        """解析 YAML → BestPracticeConfig。
        兼容两种格式：有 best_practice: 包装键（旧格式）和无包装键（新格式）。"""
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        # 兼容旧格式：如果有 best_practice: 包装键则自动解包
        if isinstance(data, dict) and "best_practice" in data and len(data) == 1:
            data = data["best_practice"]
        return load_bp_config(data)

    def _load_profiles_from_dir(self, bp_dir: Path) -> None:
        """从 bp_dir/profiles/*.json 加载 Agent Profile，
        解析 prompt_file → 读取 .md 内容 → 填入 custom_prompt，
        然后注册到 ProfileStore。"""
        if self._profile_store is None:
            return
        profiles_dir = bp_dir / "profiles"
        if not profiles_dir.is_dir():
            return
        for json_file in sorted(profiles_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                # 解析 prompt_file：弹出字段，读取 .md 内容，填入 custom_prompt
                prompt_file = data.pop("prompt_file", None)
                if prompt_file and not data.get("custom_prompt"):
                    prompt_path = bp_dir / prompt_file
                    if prompt_path.exists():
                        data["custom_prompt"] = prompt_path.read_text(encoding="utf-8")
                    else:
                        logger.warning(
                            f"Profile '{data.get('id')}' prompt_file not found: {prompt_path}"
                        )
                profile = AgentProfile.from_dict(data)
                self._profile_store.save(profile)
            except Exception as e:
                logger.warning(f"Failed to load BP profile '{json_file}': {e}")

    def get(self, bp_id: str) -> BestPracticeConfig | None:
        """按 ID 获取配置"""
        return self._configs.get(bp_id)

    def _validate(self, config: BestPracticeConfig) -> list[str]:
        """验证 BP 配置的完整性（M12 改进）"""
        errors = []

        # 1. subtask id 唯一性
        ids = [st.id for st in config.subtasks]
        if len(ids) != len(set(ids)):
            errors.append(f"Duplicate subtask IDs: {[x for x in ids if ids.count(x) > 1]}")

        # 2. depends_on 引用的 subtask_id 存在
        id_set = set(ids)
        for st in config.subtasks:
            for dep in st.depends_on:
                if dep not in id_set:
                    errors.append(f"Subtask '{st.id}' depends_on unknown '{dep}'")

        # 3. 循环依赖检测
        if any(st.depends_on for st in config.subtasks):
            cycle = self._detect_cycle(config.subtasks)
            if cycle:
                errors.append(f"Circular dependency detected: {' → '.join(cycle)}")

        # 4. input_schema 基本结构检查
        for st in config.subtasks:
            if not isinstance(st.input_schema, dict):
                errors.append(f"Subtask '{st.id}' input_schema is not a dict")
            elif st.input_schema.get("type") != "object":
                errors.append(f"Subtask '{st.id}' input_schema.type should be 'object'")

        # 5. Schema 深度检查（M11）
        for st in config.subtasks:
            depth_warnings = self._validate_schema_depth(st.input_schema, st.id)
            errors.extend(depth_warnings)

        # 6. description 非空检查（m3）
        for st in config.subtasks:
            if not st.description:
                errors.append(
                    f"Subtask '{st.id}' has empty description, "
                    f"will use name '{st.name}' as fallback"
                )

        # 7. agent_profile 存在性检查
        if self._profile_store:
            for st in config.subtasks:
                if not self._profile_store.exists(st.agent_profile):
                    errors.append(
                        f"Subtask '{st.id}' agent_profile '{st.agent_profile}' "
                        f"not found in ProfileStore"
                    )

        return errors
```

### 3.4 BP Profile 加载策略

BP Agent Profile 的加载由 `BPConfigLoader._load_profiles_from_dir()` 在 `load_all()` 时完成，
注册到全局 `ProfileStore`，使得 `orchestrator.delegate(to_agent=profile_id)` 可直接查找。

#### prompt_file 解析

BP Profile JSON 包含 `prompt_file` 字段（如 `"prompts/topic-researcher.md"`），
指向同 BP 目录下的 Markdown 文件。加载时：

1. 从 JSON 数据中弹出 `prompt_file` 字段
2. 以 BP 目录为基准解析相对路径，读取 `.md` 文件内容
3. 填入 `custom_prompt` 字段（仅当 `custom_prompt` 为空时）
4. 调用 `AgentProfile.from_dict(data)` 构造 Profile
5. 调用 `ProfileStore.save(profile)` 注册

`AgentProfile` 本身不需要 `prompt_file` 字段。`from_dict()` 的字段过滤机制会忽略未知字段。

#### _shared/ 目录

`_shared/profiles/` 在所有 BP 子目录之前加载，其 `prompt_file` 解析基准为 `_shared/` 目录。
共享 Profile 可被多个 BP 的 `subtask.agent_profile` 引用。
若 BP 子目录中有同 `id` 的 Profile，会覆盖 `_shared/` 中的同名 Profile（后加载优先）。

#### 枚举值大小写

Profile JSON 中的枚举字段使用小写值：
- `"type": "custom"`（对应 `AgentType.CUSTOM`）
- `"skills_mode": "inclusive"`（对应 `SkillsMode.INCLUSIVE`）

与 `AgentType` 和 `SkillsMode` 枚举的 `.value` 一致。

### 3.5 组件生命周期与所有权

```
初始化链（应用启动时）:

Agent.__init__()
  │
  ├─ BPToolHandler(agent)          ← **条件注册**：仅当 BP 配置子目录存在时注册（M5 改进）
  │     └─ 延迟初始化：首次工具调用时通过 get_bp_engine() 获取单例
  │
  └─ PromptAssembler(..., bp_engine=None)
        └─ bp_engine 延迟绑定：首次 build_system_prompt 调用时通过 get_bp_engine() 获取

**条件注册**（M5 改进）：
- 只有当搜索路径下存在 BP 配置子目录时才注册 BPToolHandler
- 避免无 BP 配置时 bp_* 工具出现在 LLM tool catalog 中浪费 token
- 检测逻辑：
  ```python
  any(
      (d / "config.yaml").exists()
      for p in search_paths if p.is_dir()
      for d in p.iterdir()
      if d.is_dir() and not d.name.startswith(("_", "."))
  )
  ```

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

def get_bp_engine(profile_store: "ProfileStore | None" = None) -> BPEngine:
    """返回进程级 BPEngine 单例。BPToolHandler 和 PromptAssembler 共享同一实例。
    首次调用时需要 profile_store 参数（用于加载 BP Agent Profile）。"""
    global _bp_engine
    if _bp_engine is None:
        if profile_store is None:
            # 从 orchestrator 获取（延迟导入避免循环依赖）
            import seeagent.main as _main
            profile_store = getattr(
                getattr(_main, "_orchestrator", None), "_profile_store", None
            )
        state_manager = BPStateManager()
        _bp_engine = BPEngine(
            state_manager=state_manager,
            profile_store=profile_store,
        )
    return _bp_engine
```

**所有权规则**：

| 组件 | 所有者 | 生命周期 | 说明 |
|------|--------|---------|------|
| `BPStateManager` | `get_bp_engine()` 单例工厂 | 进程级 | 管理所有会话的 BP 实例状态 |
| `BPEngine` | `get_bp_engine()` 单例工厂 | 进程级 | 编排逻辑，无状态（状态在 BPStateManager 中） |
| `BPToolHandler` | `SystemHandlerRegistry` | 进程级 | 薄层，通过 `get_bp_engine()` 获取共享 engine |
| `PromptAssembler` | `Agent` | 进程级 | 通过 `_get_bp_engine()` 延迟获取共享 engine |
| `BPConfigLoader` | `BPEngine` | 进程级 | 配置缓存 + Profile 注册，启动时加载 |
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
    - 内存存储 + Session.metadata 持久化（C3 改进）
    - 通过 SessionManager 的 5s 防抖写入机制实现持久化
    """

    def __init__(self) -> None:
        self._instances: dict[str, BPInstanceSnapshot] = {}  # instance_id → snapshot
        self._session_index: dict[str, list[str]] = {}       # session_id → [instance_ids]
        self._active_map: dict[str, str | None] = {}          # session_id → active_instance_id
        self._lock = asyncio.Lock()  # 必须用 asyncio.Lock，系统全异步
        self._pending_switches: dict[str, PendingContextSwitch] = {}  # session_id → switch（C2 改进）
        self._cooldowns: dict[str, int] = {}  # session_id → remaining_turns（M4/M13 改进）

    # ── 持久化（C3 改进）──

    async def persist(self, session: "Session") -> None:
        """将该会话的所有 BP 实例序列化到 Session.metadata。
        利用 SessionManager 已有的 5s 防抖写入机制，不会产生 I/O 热点。"""
        async with self._lock:
            session_id = session.id
            instance_ids = self._session_index.get(session_id, [])
            snapshots = {}
            for iid in instance_ids:
                snap = self._instances.get(iid)
                if snap:
                    snapshots[iid] = self._serialize_snapshot(snap)

            session.metadata["_bp_state"] = {
                "instances": snapshots,
                "active_id": self._active_map.get(session_id),
                "cooldown": self._cooldowns.get(session_id, 0),  # M13 改进
                "version": 1,
            }

    def restore_from_session(self, session: "Session") -> int:
        """从 Session.metadata 恢复 BP 实例状态。
        返回恢复的实例数量。应在 Session 加载时调用。"""
        saved = session.metadata.get("_bp_state")
        if not saved or saved.get("version") != 1:
            return 0

        count = 0
        for iid, data in saved.get("instances", {}).items():
            try:
                snap = self._deserialize_snapshot(data, session.id)
                if snap:
                    self._instances[iid] = snap
                    self._session_index.setdefault(session.id, []).append(iid)
                    count += 1
            except Exception as e:
                logger.warning(f"Failed to restore BP instance {iid}: {e}")

        active_id = saved.get("active_id")
        if active_id and active_id in self._instances:
            self._active_map[session.id] = active_id

        cooldown = saved.get("cooldown", 0)
        if cooldown > 0:
            self._cooldowns[session.id] = cooldown

        return count

    # ── 序列化/反序列化（H1 改进）──

    @staticmethod
    def _serialize_snapshot(snap: BPInstanceSnapshot) -> dict:
        """将快照序列化为可 JSON 化的 dict。
        排除 bp_config（运行时引用，恢复时由 BPEngine.restore_session 关联）。
        枚举字段序列化为 .value。"""
        return {
            "bp_id": snap.bp_id,
            "instance_id": snap.instance_id,
            "session_id": snap.session_id,
            "status": snap.status.value,
            "created_at": snap.created_at,
            "completed_at": snap.completed_at,
            "suspended_at": snap.suspended_at,
            "current_subtask_index": snap.current_subtask_index,
            "run_mode": snap.run_mode.value,
            "subtask_statuses": {k: v.value for k, v in snap.subtask_statuses.items()},
            "initial_input": snap.initial_input,
            "subtask_outputs": snap.subtask_outputs,
            "context_summary": snap.context_summary,
            # 注意：不序列化 bp_config，恢复时由 BPEngine.restore_session 关联
        }

    @staticmethod
    def _deserialize_snapshot(data: dict, session_id: str) -> BPInstanceSnapshot | None:
        """从序列化 dict 重建快照。枚举字段从 .value 恢复。"""
        try:
            return BPInstanceSnapshot(
                bp_id=data["bp_id"],
                instance_id=data["instance_id"],
                session_id=session_id,
                status=BPStatus(data.get("status", "active")),
                created_at=data.get("created_at", 0.0),
                completed_at=data.get("completed_at"),
                suspended_at=data.get("suspended_at"),
                current_subtask_index=data.get("current_subtask_index", 0),
                run_mode=RunMode(data.get("run_mode", "manual")),
                subtask_statuses={
                    k: SubtaskStatus(v)
                    for k, v in data.get("subtask_statuses", {}).items()
                },
                initial_input=data.get("initial_input", {}),
                subtask_outputs=data.get("subtask_outputs", {}),
                context_summary=data.get("context_summary", ""),
                bp_config=None,  # 由 BPEngine.restore_session 关联
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to deserialize BP snapshot: {e}")
            return None

    # ── Pending 上下文切换（C2 改进）──

    def set_pending_switch(
        self, session_id: str, switch: PendingContextSwitch,
    ) -> None:
        """设置待执行的上下文切换"""
        self._pending_switches[session_id] = switch

    def consume_pending_switch(
        self, session_id: str,
    ) -> PendingContextSwitch | None:
        """消费并清除待执行的上下文切换，返回 None 表示无待执行切换"""
        return self._pending_switches.pop(session_id, None)

    def has_pending_switch(self, session_id: str) -> bool:
        return session_id in self._pending_switches

    # ── 推断冷却管理（M4/M13 改进）──

    def set_cooldown(self, session_id: str, turns: int = 5) -> None:
        """设置推断冷却（选择自由模式后）"""
        self._cooldowns[session_id] = turns

    def tick_cooldown(self, session_id: str) -> None:
        """每轮用户输入递减"""
        if session_id in self._cooldowns:
            self._cooldowns[session_id] = max(0, self._cooldowns[session_id] - 1)
            if self._cooldowns[session_id] == 0:
                del self._cooldowns[session_id]

    def get_cooldown(self, session_id: str) -> int:
        return self._cooldowns.get(session_id, 0)

    # ── 生命周期 ──
    # 注：以下方法在当前线性模式下由调用方保证串行访问（同一会话的请求通过 busy_lock 串行化）。
    # DAG 扩展引入并行子任务后，需为所有写方法加 async with self._lock。（M3 改进）

    async def create_instance(
        self,
        bp_config: BestPracticeConfig,
        session_id: str,
    ) -> BPInstanceSnapshot:
        """创建新实例，设为该会话的活跃实例"""

    async def suspend(
        self,
        instance_id: str,
        context_summary: str,
        master_messages: list[dict] | None = None,
    ) -> None:
        """挂起实例：保存上下文摘要，状态 → SUSPENDED"""

    async def resume(self, instance_id: str) -> BPInstanceSnapshot:
        """恢复实例：状态 → ACTIVE，返回快照"""

    async def complete(self, instance_id: str) -> None:
        """标记实例完成"""

    async def cancel(self, instance_id: str) -> None:
        """取消实例：状态 → CANCELLED，清除 active_map（H5 改进）"""

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
        """生成系统提示注入的状态表文本（S2: 包含 input_preview 区分同配置多实例）"""
        rows = []
        for inst in self.get_all_for_session(session_id):
            # S2: 添加输入参数摘要用于区分同配置多实例
            input_preview = ""
            if inst.initial_input:
                topic = inst.initial_input.get("topic", "")
                if topic:
                    input_preview = f" ({topic[:30]})"
                else:
                    first_val = (
                        str(list(inst.initial_input.values())[0])[:30]
                        if inst.initial_input else ""
                    )
                    input_preview = f" ({first_val})" if first_val else ""
            rows.append(f"| {inst.bp_name}{input_preview} | {inst.instance_id[:8]} | ...")
        return "\n".join(rows)

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
│  ├── 内存存储 + Session.metadata 持久化（C3 改进）              │
│  └── pending_switches / cooldowns 管理（C2/M13 改进）          │
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
6. **自动模式**：当 bp_start 或 bp_continue 的返回结果中包含
   "当前为自动模式，请立即调用 bp_continue" 时，
   你必须**立即**调用 bp_continue，不要等待用户操作。
   但如果用户在此期间发送了消息，优先处理用户消息。（C1 改进）
7. **Chat-to-Edit 确认**：当 bp_edit_output 调用前，如果修改意图可能有歧义，
   先通过 ask_user 确认修改范围。（S4 改进）
8. **Chat-to-Edit 流程**：修改子任务输出前，先调用 bp_get_output 获取完整当前值，
   再基于完整数据生成 changes JSON，最后调用 bp_edit_output。（H2 改进）
9. **取消任务**：用户明确表示要取消/放弃当前任务时，调用 bp_cancel 终止实例。（H5 改进）

### 自动模式中断规则（S1 改进）
- 如果在自动执行过程中收到用户消息，**优先处理用户消息**
- 如果用户表达了暂停、取消或修改意图，停止自动执行
- 处理完用户消息后，如果任务未取消，可询问用户是否继续自动执行
```

#### `system_dynamic.md` — 注入到系统提示动态段（含条件性意图路由指令，M3 改进）

```markdown
## 当前最佳实践状态

> 以下子任务输出数据为最新版本。如与上文 tool_result 中的旧数据冲突，以此为准。（H3 改进）

$status_table

$active_context

$intent_routing_instruction
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

#### `chat_to_edit.md` — Deep Merge 指令（M2 改进：明确数组语义）

```markdown
用户要求修改子任务「$subtask_name」的输出。

当前输出：
```json
$current_output
```

修改意图：$user_message

请生成修改后的 changes JSON，规则如下：
- 仅包含**需要修改的字段**
- 未提及的字段不要包含在 changes 中，它们将保持原值
- **对象字段**：递归合并（仅覆盖提及的子字段）
- **数组字段**：提供**完整的新数组**（数组不支持部分修改，必须给出完整替换值）
- **删除字段**：将字段值设为 null

示例：
用户说 "把盈利模式改成纯SaaS"
当前 findings = ["技术架构：TokenML引擎", "盈利模式：SaaS+抽成", "竞争壁垒：3项专利"]
正确的 changes = {
  "findings": ["技术架构：TokenML引擎", "盈利模式：纯SaaS订阅", "竞争壁垒：3项专利"]
}
（注意：完整数组，包含未修改的元素）

请输出 changes JSON。
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
    └─ 推进到下一个子任务，根据 run_mode 返回不同 tool_result：
        ├─ auto 模式 → tool_result 含 "请立即调用 bp_continue" 指令，
        │   MasterAgent 收到后自动调用下一个 bp_continue（C1 改进：迭代式执行）
        └─ manual 模式 → 返回完成信息，引导 MasterAgent 展示交互按钮
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

    def __init__(
        self,
        state_manager: "BPStateManager",
        profile_store: "ProfileStore | None" = None,
    ) -> None:
        self._state_manager = state_manager
        self._config_loader = BPConfigLoader(
            search_paths=[
                Path(__file__).parent / "configs",       # 系统内置
                Path(project_root) / "best_practice",    # 用户自定义（注意下划线）
            ],
            profile_store=profile_store,
        )
        self._config_loader.load_all()  # 加载配置 + 注册 Agent Profile
        self._prompt_loader = PromptTemplateLoader()
        self._schema_chain = SchemaChain()
        self._context_bridge: ContextBridge | None = None  # 延迟创建（依赖 ContextManager）

    @property
    def state_manager(self) -> "BPStateManager":
        """公开属性，供 BPToolHandler 访问（m2 改进：消除私有成员访问）"""
        return self._state_manager

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
        # 注意：同一 session 中多个 BP 实例共享同一个 AgentInstancePool 缓存。
        # 这是安全的，因为 AgentOrchestrator._call_agent() 每次都以
        # session_messages=[] 调用 SubAgent，Brain.Context 每次全新构建。
        # AgentState._tasks 按 session_id 隔离，delegate() 内部管理 begin/reset。
        # 如果未来需要跨子任务保留 SubAgent 上下文（如 DAG 扩展中的迭代执行），
        # 需要为每个 (bp_instance_id, subtask_id) 创建独立的 pool key。（M1 改进）
        max_attempts = subtask.max_retries + 1  # M9 改进
        last_error = None

        for attempt in range(max_attempts):
            try:
                result = await orchestrator.delegate(
                    session=session,
                    from_agent="main",
                    to_agent=subtask.agent_profile,
                    message=message,
                    reason=f"BP:{config.name} / {subtask.name}",
                )
                last_error = None
                break
            except Exception as e:
                last_error = e
                if attempt < max_attempts - 1:
                    logger.warning(
                        f"SubTask '{subtask.id}' failed (attempt {attempt + 1}/{max_attempts}): {e}"
                    )
                    await asyncio.sleep(2 ** attempt)  # 指数退避
                else:
                    logger.error(
                        f"SubTask '{subtask.id}' failed after {max_attempts} attempts: {e}"
                    )

        if last_error:
            # H7 改进：标记为 FAILED（区分于 PENDING 的"从未执行"状态）
            # current_subtask_index 不推进，bp_continue 重试时仍指向当前子任务
            self._state_manager.update_subtask_status(instance_id, subtask.id, SubtaskStatus.FAILED)
            return (
                f"子任务「{subtask.name}」执行失败（尝试 {max_attempts} 次）: {last_error}\n"
                f"子任务已标记为 FAILED。可通过 bp_continue 重试，或使用 bp_cancel 取消整个任务。"
            )

        # 解析输出
        output = self._parse_output_json(result)
        self._state_manager.update_subtask_output(instance_id, subtask.id, output)

        # 发射事件
        await self._emit_bp_events(instance_id, subtask.id, output, session)

        # 持久化（C3 改进）
        await self._state_manager.persist(session)

        # ── C1 改进：不再递归，统一返回 ──
        if idx >= len(config.subtasks) - 1:
            self._state_manager.complete(instance_id)
            await self._state_manager.persist(session)
            return self._format_completion_result(instance)

        self._state_manager.advance_subtask(instance_id)

        # 根据 run_mode 返回不同的 tool_result
        # MasterAgent 收到后根据内容决定行为
        return self._format_subtask_complete_result(instance, subtask, output)

    def _format_subtask_complete_result(
        self,
        instance: BPInstanceSnapshot,
        subtask: SubtaskConfig,
        output: dict,
    ) -> str:
        """
        格式化子任务完成结果。
        手动和自动模式返回不同指令，引导 MasterAgent 做出正确行为。（C1 改进）
        """
        next_subtask = instance.bp_config.subtasks[instance.current_subtask_index]
        output_preview = json.dumps(output, ensure_ascii=False)[:200]

        if instance.run_mode == RunMode.AUTO:
            # 自动模式：指令 MasterAgent 立即调用 bp_continue
            return (
                f"子任务「{subtask.name}」已完成。输出预览: {output_preview}\n"
                f"当前为自动模式，请立即调用 bp_continue("
                f"instance_id=\"{instance.instance_id}\") "
                f"执行下一个子任务「{next_subtask.name}」。"
            )
        else:
            # 手动模式：指令 MasterAgent 展示交互按钮
            return (
                f"子任务「{subtask.name}」已完成。输出预览: {output_preview}\n"
                f"下一个子任务: 「{next_subtask.name}」\n"
                f"请使用 ask_user 展示以下选项让用户选择：\n"
                f"- [查看结果]（纯 UI 操作，不生成消息）\n"
                f"- [进入下一步]（用户确认后调用 bp_continue）"
            )

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
    ) -> list[str]:
        """
        宽松校验子任务输出是否符合下游 input_schema。
        返回缺失的 required 字段列表（空列表表示通过）。（M10 改进）
        不阻断操作，仅记录 warning 供调试。
        """
        config = instance.bp_config
        idx = next((i for i, s in enumerate(config.subtasks) if s.id == subtask_id), None)
        if idx is None or idx >= len(config.subtasks) - 1:
            return []  # 最后一个子任务无下游约束

        next_schema = config.subtasks[idx + 1].input_schema
        missing = [k for k in next_schema.get("required", []) if k not in output]
        if missing:
            logger.warning(
                f"[BPEngine] Edited output for '{subtask_id}' missing required fields "
                f"for downstream: {missing}"
            )
        return missing

    def get_subtask_names(
        self, instance: BPInstanceSnapshot, subtask_ids: list[str],
    ) -> list[str]:
        """将 subtask_id 列表转换为显示名（M10 改进）"""
        name_map = {st.id: st.name for st in instance.bp_config.subtasks}
        return [f"「{name_map.get(sid, sid)}」" for sid in subtask_ids]

    def _resolve_input(
        self,
        instance: BPInstanceSnapshot,
        subtask: SubtaskConfig,
    ) -> dict:
        """
        解析子任务的输入数据。抽象为独立方法，线性/DAG 通用。

        线性模式（当前）：
          - 第一个子任务 → initial_input（M8 改进）
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
            return instance.initial_input  # M8 改进：使用独立字段
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

    async def emit_task_switch(
        self, session: "Session", suspended_id: str, activated_id: str,
    ) -> None:
        """发射 bp_task_switch 事件（C2 改进：前端提前通知）。"""

    # ── Session 恢复（C3 改进）──

    def restore_session(self, session: "Session") -> int:
        """从 Session 恢复 BP 实例状态，关联 bp_config 引用。
        应在 Session 加载/激活时调用。"""
        count = self._state_manager.restore_from_session(session)
        if count > 0:
            # 关联 bp_config
            for iid in self._state_manager._session_index.get(session.id, []):
                snap = self._state_manager.get(iid)
                if snap and snap.bp_config is None:
                    config = self._config_loader.get(snap.bp_id)
                    if config:
                        snap.bp_config = config
                    else:
                        logger.warning(
                            f"BP config '{snap.bp_id}' not found for instance {iid}, "
                            f"marking as completed"
                        )
                        snap.status = BPStatus.COMPLETED
        return count

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

**H3 改进：Chat-to-Edit 数据一致性**

编辑子任务输出后，`BPStateManager.subtask_outputs` 已更新，但 `Brain.Context.messages` 中的旧 `tool_result` 仍包含修改前的值。为避免 MasterAgent 基于过时数据推理：

1. `bp_edit_output` 的 `tool_result` 中返回**完整的修改后 output**（不仅是 diff），使 Brain.Context 最新消息包含正确数据
2. `system_dynamic.md` 模板中注入提示：「以下子任务输出为最新版本，如与上文 tool_result 冲突，以此为准」
3. MasterAgent 在 Chat-to-Edit 前应先调用 `bp_get_output` 获取完整当前值（H2 改进），再生成 `changes` JSON

### 6.4 多任务切换（C2 改进：安全钩子点）

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
  ├─ 1. 设置 PendingContextSwitch（不直接操作上下文）
  │     state_manager.set_pending_switch(session_id, PendingContextSwitch(
  │       suspended_instance_id=current.instance_id,
  │       target_instance_id="bp-002",
  │     ))
  │
  ├─ 2. 发射前端事件（提前通知）：
  │     bp_task_switch (活跃任务即将变更)
  │
  └─ 3. 返回信息性结果给 MasterAgent（不包含上下文操作）：
        ├─ suspended → "已准备切换。上下文将在下一轮推理时自动切换。"
        └─ completed → "已准备切换。任务已完成。"

下一轮推理准备阶段（_prepare_session_context）：
  │
  ├─ 检测 pending_switch → 消费
  │
  ├─ context_bridge.execute_pending_switch()：
  │   ├─ 1. 压缩当前上下文 → context_summary（失败时使用 _fallback_summary）
  │   ├─ 2. state_manager.suspend(current_id, context_summary)
  │   ├─ 3. state_manager.resume(target_id) → snapshot
  │   ├─ 4. prepare_restore_messages(snapshot) → restore_messages
  │   ├─ 5. Brain.Context.messages 清空 + 注入 restore_messages（此时安全）
  │   └─ 6. persist(session)（C3 改进）
  │
  └─ 继续正常推理...
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
        # M1 改进：每个子任务 output 最多 2000 字符，避免恢复消息导致 token 溢出。
        # 超过阈值时截断并提示通过 bp_get_output 查看完整数据。
        MAX_OUTPUT_CHARS = 2000
        completed_lines = []
        for subtask in snapshot.bp_config.subtasks[:snapshot.current_subtask_index]:
            output = snapshot.subtask_outputs.get(subtask.id, {})
            output_json = json.dumps(output, ensure_ascii=False, indent=2)
            if len(output_json) > MAX_OUTPUT_CHARS:
                output_json = (
                    output_json[:MAX_OUTPUT_CHARS]
                    + f"\n... [截断，完整数据请使用 bp_get_output(subtask_id=\"{subtask.id}\") 查看]"
                )
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

    async def execute_pending_switch(
        self,
        switch: PendingContextSwitch,
        brain_context: "BrainContext",
        session: "Session",
        state_manager: "BPStateManager",
    ) -> None:
        """
        在安全时机（推理准备阶段）执行上下文切换。（C2 改进）
        此时 Brain.Context 不在被 ReasoningEngine 使用中，修改是安全的。
        """
        # 1. 压缩当前上下文 → contextSummary
        current_messages = list(brain_context.messages)
        try:
            summary = await self.compress_for_suspend(
                current_messages,
                system_prompt=brain_context.system or "",
            )
        except Exception as e:
            # m1 改进：压缩失败降级
            logger.warning(f"Context compression failed, using fallback: {e}")
            summary = self._fallback_summary(current_messages)

        # 2. 挂起当前实例
        state_manager.suspend(
            switch.suspended_instance_id,
            context_summary=summary,
        )

        # 3. 恢复目标实例
        target_snapshot = state_manager.resume(switch.target_instance_id)

        # 4. 构建恢复消息
        restore_messages = self.prepare_restore_messages(target_snapshot)

        # 5. 替换 Brain.Context（此时安全）
        brain_context.messages.clear()
        brain_context.messages.extend(restore_messages)

        # 6. 持久化（C3 改进）
        await state_manager.persist(session)

    def _fallback_summary(self, messages: list[dict]) -> str:
        """m1 改进：压缩失败时的降级方案 — 截取最后几条消息的文本"""
        recent = messages[-5:] if len(messages) > 5 else messages
        lines = []
        for m in recent:
            role = m.get("role", "?")
            content = str(m.get("content", ""))[:200]
            lines.append(f"[{role}] {content}")
        return "（上下文压缩失败，以下为最近对话片段）\n" + "\n".join(lines)
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
- SubAgent 可调用 `ask_user` 工具向用户提问。该调用通过 `session.context._sse_event_bus` 发送 `ask_user` SSE 事件到前端，用户回复通过 `pending_user_inserts` 注入 SubAgent 的 `TaskState`，由 `AgentOrchestrator` 的已有机制路由。SubAgent 的 `ask_user` 不影响 MasterAgent 的对话流。（S3 改进）

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
    "current_subtask_index": 1,                # L1 改进：冗余字段，简化前端高亮逻辑
    "run_mode": "manual",
    "status": "active",                        # H5 改进：实例级状态（active/suspended/completed/cancelled）
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

### 9.4 SSE 重连全量状态同步 API（M7 改进）

前端 SSE 连接断开后重连时，需要获取当前所有 BP 实例的全量状态：

```python
# api/routes/seecrab.py — 新增 endpoint
@router.get("/api/bp/status/{conversation_id}")
async def get_bp_status(conversation_id: str) -> dict:
    """前端 SSE 重连后调用，获取所有 BP 实例的全量状态。"""
    session = session_manager.get_by_conversation(conversation_id)
    if not session:
        return {"instances": [], "active_id": None}

    try:
        from seeagent.bestpractice import get_bp_engine
        engine = get_bp_engine()
    except ImportError:
        return {"instances": [], "active_id": None}

    state = engine._state_manager
    instances = state.get_all_for_session(session.id)
    active = state.get_active(session.id)

    return {
        "active_id": active.instance_id if active else None,
        "instances": [
            {
                "instance_id": inst.instance_id,
                "bp_id": inst.bp_id,
                "bp_name": inst.bp_config.name if inst.bp_config else inst.bp_id,
                "status": inst.status.value,
                "run_mode": inst.run_mode.value,
                "current_subtask_index": inst.current_subtask_index,
                "subtasks": [
                    {"id": st.id, "name": st.name}
                    for st in (inst.bp_config.subtasks if inst.bp_config else [])
                ],
                "statuses": {k: v.value for k, v in inst.subtask_statuses.items()},
                "outputs": inst.subtask_outputs,
            }
            for inst in instances
        ],
    }
```

### 9.5 非 SeeCrab 通道降级策略（S5 改进）

BP 功能依赖 SSE 事件（`bp_progress`、`bp_subtask_output` 等）。对于非 SeeCrab 通道（Telegram、Feishu 等），采用以下降级策略：

| 功能 | SeeCrab（完整） | 非 SeeCrab 通道（降级） |
|------|----------------|----------------------|
| 进度展示 | SSE → TaskProgressCard | tool_result 文本中包含进度摘要 |
| 输出编辑 | SubtaskOutputPanel + Chat-to-Edit | 仅支持 Chat-to-Edit（通过自然语言） |
| 任务切换 | bp_task_switch SSE | 通过对话指令切换 |
| 实时步骤 | StepCard + ThinkingBlock | 仅最终文本输出 |

降级检测在 `BPEngine._emit_event` 中实现：

```python
async def _emit_event(self, event_type: str, data: dict, session: "Session") -> None:
    event_bus = getattr(session.context, "_sse_event_bus", None)
    if event_bus is not None:
        await event_bus.put({"type": event_type, **data})
    # 非 SeeCrab 通道无 event_bus，事件静默丢弃
    # 所有关键信息已包含在 tool_result 文本中
```

### 9.6 前端 REST API（H4/H8 改进）

BP 功能除 SSE 事件外，还需要以下 REST API 供前端直接调用（非 LLM 工具调用）：

```python
# api/routes/seecrab.py — 新增 BP endpoints

@router.put("/api/bp/run-mode/{conversation_id}")
async def set_run_mode(conversation_id: str, body: dict) -> dict:
    """前端切换手动/自动模式时调用（H4 改进）。
    body: { "instance_id": str, "run_mode": "manual" | "auto" }
    不生成用户消息，纯状态变更。"""
    session = session_manager.get_by_conversation(conversation_id)
    if not session:
        return {"error": "Session not found"}

    engine = get_bp_engine()
    instance = engine.state_manager.get(body["instance_id"])
    if not instance:
        return {"error": "Instance not found"}

    instance.run_mode = RunMode(body["run_mode"])
    await engine.state_manager.persist(session)
    # 发射 bp_progress 事件通知其他可能的 SSE 消费者
    await engine.emit_progress(instance.instance_id, session)
    return {"success": True}


@router.post("/api/bp/start/{conversation_id}")
async def start_bp_from_ui(conversation_id: str, body: dict) -> dict:
    """UI_CLICK 触发：前端直接启动 BP 任务（H8 改进）。
    body: { "bp_id": str, "input_data"?: dict }
    构造用户消息注入到会话，触发 MasterAgent 调用 bp_start。"""
    session = session_manager.get_by_conversation(conversation_id)
    if not session:
        return {"error": "Session not found"}

    # 构造用户消息并注入
    bp_id = body["bp_id"]
    input_data = body.get("input_data", {})
    message = f"请执行最佳实践「{bp_id}」"
    if input_data:
        message += f"，输入参数：{json.dumps(input_data, ensure_ascii=False)}"

    # 通过 pending_user_inserts 或直接发起新请求
    # 实现取决于 Agent 是否当前空闲（idle vs busy）
    await inject_user_message(session, message)
    return {"success": True, "message": message}
```

---

## 10. 工具定义

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
                "input_data": {"type": "object", "description": "初始输入数据（可为空，由子任务通过 ask_user 补充）"},
            },
            "required": ["bp_id"],  # input_data 可选（M5 改进）
        },
        "category": "BestPractice",
        "triggers": ["用户确认进入最佳实践模式时调用"],
    },
    {
        "name": "bp_continue",
        "description": "继续执行当前活跃最佳实践的下一个子任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID。不提供则使用当前活跃实例。",
                },
            },
            "required": [],  # 全部可选（M5 改进）
        },
        "category": "BestPractice",
    },
    {
        "name": "bp_edit_output",
        "description": "编辑已完成子任务的输出。对象字段递归合并，数组字段整体替换。",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID。不提供则使用当前活跃实例。",
                },
                "subtask_id": {"type": "string"},
                "changes": {
                    "type": "object",
                    "description": "要修改的字段及新值。对象字段递归合并，数组字段提供完整新数组。（M2 改进）",
                },
            },
            "required": ["subtask_id", "changes"],  # instance_id 可选（M5 改进）
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
            "required": ["target_instance_id"],  # 切换目标必须明确
        },
        "category": "BestPractice",
    },
    # ── H2 改进：新增只读工具 ──
    {
        "name": "bp_get_output",
        "description": "获取已完成子任务的完整输出 JSON（Chat-to-Edit 前调用，获取当前值）",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID。不提供则使用当前活跃实例。",
                },
                "subtask_id": {"type": "string", "description": "子任务 ID"},
            },
            "required": ["subtask_id"],
        },
        "category": "BestPractice",
    },
    # ── H5 改进：新增取消工具 ──
    {
        "name": "bp_cancel",
        "description": "取消最佳实践任务。如果有正在执行的子任务会等待其完成后取消。",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID。不提供则取消当前活跃实例。",
                },
            },
            "required": [],
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

    TOOLS = ["bp_start", "bp_continue", "bp_edit_output", "bp_switch_task",
             "bp_get_output", "bp_cancel"]

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
            self._state = self._engine.state_manager  # m2 改进：通过公开属性访问

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
            "bp_get_output": self._bp_get_output,
            "bp_cancel": self._bp_cancel,
        }.get(tool_name)
        if handler is None:
            return f"未知的 BP 工具: {tool_name}"
        return await handler(params, session=session, orchestrator=orchestrator)

    async def _bp_start(self, args: dict, *, session, orchestrator) -> str:
        config = self._engine.get_config(args["bp_id"])
        if config is None:
            return f"未找到最佳实践配置: {args['bp_id']}"

        instance = self._state.create_instance(config, session.id)

        # M8 改进：使用独立字段存储初始输入
        instance.initial_input = args.get("input_data", {})

        # 发射初始进度事件
        await self._engine.emit_progress(instance.instance_id, session)

        # 执行第一个子任务
        return await self._engine.execute_subtask(
            instance.instance_id, orchestrator, session,
        )

    def _resolve_instance_id(self, args: dict, session) -> str | None:
        """解析 instance_id，不提供时使用当前活跃实例（M5 改进）"""
        instance_id = args.get("instance_id")
        if instance_id:
            return instance_id
        active = self._state.get_active(session.id)
        return active.instance_id if active else None

    async def _bp_continue(self, args: dict, *, session, orchestrator) -> str:
        instance_id = self._resolve_instance_id(args, session)
        if not instance_id:
            return "当前没有活跃的最佳实践实例，请先调用 bp_start。"
        instance = self._state.get(instance_id)
        if instance is None:
            return "实例不存在"

        # stale 重置逻辑委托给 engine（薄层不含业务逻辑）
        self._engine.reset_stale_if_needed(instance)

        return await self._engine.execute_subtask(
            instance_id, orchestrator, session,
        )

    async def _bp_edit_output(self, args: dict, *, session, orchestrator) -> str:
        instance_id = self._resolve_instance_id(args, session)  # M5 改进
        if not instance_id:
            return "当前没有活跃的最佳实践实例。"

        old, new = self._state.merge_subtask_output(
            instance_id, args["subtask_id"], args["changes"],
        )

        # M10 改进：校验并在结果中传达
        instance = self._state.get(instance_id)
        validation_warning = ""
        if instance:
            missing = self._engine.validate_output_soft(instance, args["subtask_id"], new)
            if missing:
                validation_warning = (
                    f"\n⚠️ 修改后输出缺少下游必需字段: {missing}。"
                    f"下游子任务可能因此需要通过 ask_user 向用户补充信息。"
                )

        # 标记下游 stale（基于 subtask_id，线性/DAG 通用）
        stale_ids = self._state.mark_downstream_stale(
            instance_id, args["subtask_id"],
        )

        # 发射事件
        await self._engine.emit_stale(instance_id, stale_ids, session)
        await self._engine.emit_subtask_output(
            instance_id, args["subtask_id"], new, session,
        )

        # C3 改进：持久化
        await self._state.persist(session)

        if stale_ids:
            stale_names = self._engine.get_subtask_names(instance, stale_ids)
            return (
                f"已修改子任务「{args['subtask_id']}」的输出。{validation_warning}\n"
                f"下游子任务 {stale_names} 需基于新数据重新执行。\n"
                f"请使用 ask_user 确认用户是否继续级联重跑。"
            )
        return f"已修改子任务「{args['subtask_id']}」的输出，无需重跑下游。{validation_warning}"

    async def _bp_switch_task(self, args: dict, *, session, orchestrator) -> str:
        """C2 改进：不直接操作上下文，只设置 PendingContextSwitch"""
        target_id = args["target_instance_id"]
        target = self._state.get(target_id)
        if target is None:
            return f"目标实例不存在: {target_id}"

        current_active = self._state.get_active(session.id)
        if current_active is None:
            return "当前没有活跃的最佳实践实例"
        if current_active.instance_id == target_id:
            return "目标实例已经是当前活跃实例"

        # 设置 pending switch，实际切换在下一轮推理准备阶段执行
        self._state.set_pending_switch(
            session.id,
            PendingContextSwitch(
                suspended_instance_id=current_active.instance_id,
                target_instance_id=target_id,
                created_at=time.time(),
            ),
        )

        # 发射前端事件
        await self._engine.emit_task_switch(
            session,
            suspended_id=current_active.instance_id,
            activated_id=target_id,
        )

        # 返回信息性结果
        target_config = target.bp_config
        progress = sum(
            1 for s in target.subtask_statuses.values()
            if s == SubtaskStatus.DONE
        )
        total = len(target_config.subtasks)

        if target.status == BPStatus.COMPLETED:
            return (
                f"已准备切换到任务「{target_config.name}」（已完成）。\n"
                f"上下文将在下一轮推理时自动切换。\n"
                f"请回复用户关于该任务的信息。"
            )
        else:
            return (
                f"已准备切换到任务「{target_config.name}」"
                f"（{progress}/{total} 子任务已完成）。\n"
                f"上下文将在下一轮推理时自动切换。\n"
                f"请使用 ask_user 询问用户是否继续执行剩余子任务。"
            )

    # ── H2 改进：只读获取完整子任务输出 ──

    async def _bp_get_output(self, args: dict, *, session, orchestrator) -> str:
        """返回指定子任务的完整输出 JSON，供 MasterAgent 在 Chat-to-Edit 前查看。"""
        instance_id = self._resolve_instance_id(args, session)
        if not instance_id:
            return "当前没有活跃的最佳实践实例。"

        instance = self._state.get(instance_id)
        if instance is None:
            return "实例不存在"

        subtask_id = args["subtask_id"]
        output = instance.subtask_outputs.get(subtask_id)
        if output is None:
            return f"子任务「{subtask_id}」尚未有输出数据。"

        return json.dumps(output, ensure_ascii=False, indent=2)

    # ── H5 改进：取消 BP 实例 ──

    async def _bp_cancel(self, args: dict, *, session, orchestrator) -> str:
        """取消 BP 实例，清除活跃状态。"""
        instance_id = self._resolve_instance_id(args, session)
        if not instance_id:
            return "当前没有活跃的最佳实践实例。"

        instance = self._state.get(instance_id)
        if instance is None:
            return "实例不存在"
        if instance.status in (BPStatus.COMPLETED, BPStatus.CANCELLED):
            return f"实例已经处于 {instance.status.value} 状态，无需取消。"

        await self._state.cancel(instance_id)

        # 发射进度事件通知前端
        await self._engine.emit_progress(instance_id, session)

        # 持久化
        await self._state.persist(session)

        bp_name = instance.bp_config.name if instance.bp_config else instance.bp_id
        return f"已取消最佳实践任务「{bp_name}」。"
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
        优先使用 jsonschema 库（如果可用），否则退化为 required 字段检查。（m4 改进）
        """
        try:
            import jsonschema
            errors = []
            validator = jsonschema.Draft7Validator(schema)
            for error in validator.iter_errors(data):
                errors.append(error.message)
            return errors
        except ImportError:
            # 无 jsonschema 库时，退化为简单的 required 字段检查
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
        内容包含：当前实例状态表 + 活跃任务上下文 + 条件性意图路由指令。
        每次 LLM 调用都可能不同。（M3 改进：合并 intent_router）
        """
        instances = self._state_manager.get_all_for_session(session_id)
        if not instances:
            return ""

        status_table = self._state_manager.get_status_table(session_id)

        # M4/M13 改进：注入 cooldown 提示
        cooldown = self._state_manager.get_cooldown(session_id)
        if cooldown > 0:
            status_table += (
                f"\n⚠️ 用户 {cooldown} 轮消息内选择了自由模式，"
                f"不要推断触发最佳实践。"
            )

        active = self._state_manager.get_active(session_id)

        if active is None:
            # 有实例但无活跃的（全部 completed/suspended）
            return self._prompt_loader.render(
                "system_dynamic",
                status_table=status_table,
                active_context="",
                intent_routing_instruction=(
                    "如果用户消息提及上述任何已完成或挂起的任务，"
                    "请调用 bp_switch_task 切换到该任务。"
                ),
            )

        active_context = self._state_manager.get_outputs_summary(active.instance_id)

        # M3 改进：根据活跃实例的当前状态决定路由指令
        current_subtask_id = active.bp_config.subtasks[
            active.current_subtask_index
        ].id if active.current_subtask_index < len(active.bp_config.subtasks) else None

        at_pause_point = (
            current_subtask_id
            and active.subtask_statuses.get(current_subtask_id) != SubtaskStatus.CURRENT
        )

        if at_pause_point:
            intent_instruction = (
                "用户在暂停点发送消息时，请判断意图：\n"
                "A) 修改某个已完成子任务的输出 → 调用 bp_edit_output\n"
                "B) 确认进入下一步 → 调用 bp_continue\n"
                "C) 切换到其他任务 → 调用 bp_switch_task\n"
                "D) 与当前任务相关的追问 → 直接回答\n"
                "E) 无关话题 / 全新任务 → 正常处理，可能触发新的最佳实践"
            )
        else:
            intent_instruction = ""

        return self._prompt_loader.render(
            "system_dynamic",
            status_table=status_table,
            active_context=active_context,
            intent_routing_instruction=intent_instruction,
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

## 13. 触发机制（M4 改进：纯 LLM 触发）

> **设计决策变更**：删除 `BPTriggerDetector`（原 `trigger.py`），所有触发检测交由 LLM 完成。

### 13.1 触发方式

| 触发类型 | 机制 | 说明 |
|---------|------|------|
| COMMAND | LLM 在系统提示中看到 BP 定义的 `pattern`，直接识别并调用 `bp_start` | 高置信度，无需预筛 |
| CONTEXT | LLM 根据 `conditions` 关键词判断，通过 `ask_user` 让用户选择 | 最终决策在 LLM |
| CRON | 复用已有 `ScheduledTask` + CRON，在 `prompt` 中注入 `bp_start` 指令 | 不涉及 LLM 触发 |
| EVENT | 通过 `pending_user_inserts` 注入触发消息 | MasterAgent 识别后调用 |
| UI_CLICK | 前端 API 直接构造 `bp_start` 参数 | 不涉及 LLM 触发 |

### 13.2 推断冷却（inferCooldown）

冷却管理已移至 `BPStateManager`（见 §4.1），通过 `persist()` 持久化到 `Session.metadata`（M13 改进）。

- `set_cooldown(session_id, turns=5)` — 用户选择自由模式后调用
- `tick_cooldown(session_id)` — 每轮用户输入递减
- `get_cooldown(session_id)` — 在 `get_dynamic_prompt_section` 中检查

冷却期间，`get_dynamic_prompt_section` 在状态表末尾注入提示：
```
⚠️ 用户 N 轮消息内选择了自由模式，不要推断触发最佳实践。
```

### 13.3 CRON 触发完整链路（H6 改进）

CRON 触发复用已有 `ScheduledTask` 机制，完整链路如下：

```
BP 配置中声明 CRON 触发：
  triggers:
    - type: schedule
      cron: "0 1 * * 1"
        │
        ▼
BPEngine.register_cron_triggers()（启动时调用）
  │
  ├─ 遍历所有 BestPracticeConfig.triggers
  ├─ 筛选 type == "schedule"
  └─ 为每个 CRON 触发创建 ScheduledTask：
      ScheduledTask(
        trigger_type=TriggerType.CRON,
        trigger_config={"cron": "0 1 * * 1"},
        task_type=TaskType.TASK,
        prompt="请执行最佳实践「{bp_id}」，调用 bp_start(bp_id=\"{bp_id}\")",
        agent_profile_id="default",  # 由 MasterAgent 执行
      )
        │
        ▼
SchedulerEngine（已有）
  ├─ CRON 到期 → 执行 prompt
  ├─ 查找目标 session（使用默认/最近活跃 session）
  └─ 如果 Agent idle：创建新的 chat_with_session 请求
     如果 Agent busy：通过 pending_user_inserts 注入
        │
        ▼
MasterAgent 收到 prompt → 识别 bp_start 指令 → 调用 bp_start 工具
```

**关键点**：
- CRON 触发的 `prompt` 字段显式包含 `bp_start` 调用指令，MasterAgent 无需推断
- 使用 `agent_profile_id="default"` 确保由 MasterAgent 处理
- Session 选择策略：优先使用指定 `conversation_id`，否则使用最近活跃 session

### 13.4 EVENT 触发完整链路（H6 改进）

EVENT 触发通过外部事件回调注入消息：

```
外部事件到达（webhook / 内部事件总线）
  │
  ▼
EventHandler.on_event(event_name, payload)
  │
  ├─ 查找匹配的 BP 配置（triggers 中 type=="event" 且 event==event_name）
  ├─ 确定目标 session（event payload 中的 session_id 或 conversation_id）
  │
  ├─ 如果 Agent 有活跃 task：
  │   └─ task_state.pending_user_inserts.append(
  │       f"[系统事件] {event_name} 触发，请执行最佳实践「{bp_id}」，"
  │       f"调用 bp_start(bp_id=\"{bp_id}\", input_data={payload})"
  │     )
  │
  └─ 如果 Agent idle（无活跃 task）：
      └─ 发起新的 chat_with_session 请求，消息内容同上
```

**关键点**：
- idle 状态下不能使用 `pending_user_inserts`（TaskState 不存在），需发起新请求
- event payload 作为 `input_data` 传入 `bp_start`
- 安全考虑：外部事件来源需要鉴权，防止未授权的 BP 触发

### 13.5 理由

1. LLM 在系统提示中已有完整的 BP 定义和触发条件
2. COMMAND 触发由 LLM 直接识别，不需要规则预筛
3. CONTEXT 触发最终也依赖 LLM 判断
4. CRON/EVENT 通过显式 prompt 指令避免 LLM 推断
5. 减少代码量和维护负担

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
| 自动模式实现 | A) MasterAgent 驱动迭代 / B) 工具内递归 | **A) MasterAgent 驱动迭代** | tool_result 含指令引导 MasterAgent 自动调用 bp_continue，每个子任务间 MasterAgent ReAct 循环可响应 cancel/skip/user_insert（C1 改进）。**Trade-off**：自动执行 N 个子任务需额外 N 次 MasterAgent LLM 调用（每次约 2K-5K output tokens），是安全性换 token 成本（M4 改进） |
| 状态存储 | A) 扩展 AgentState / B) 扩展 SessionContext / C) 独立 | **C) 独立 BPStateManager** | 低耦合，清晰职责边界，可独立演进。通过 Session.metadata 持久化（C3 改进） |
| Prompt 注入方式 | A) 硬编码 / B) 模板文件 | **B) 模板文件** | 可维护性，非工程师也可调整 prompt |
| 上下文切换 | A) 追加标记 / B) 清空替换 / C) 延迟切换 | **C) 延迟切换（PendingContextSwitch）** | 工具调用期间不直接操作 Brain.Context，改为在推理准备阶段安全执行（C2 改进） |
| 触发检测 | A) 纯 LLM / B) 规则预筛 + LLM 确认 | **A) 纯 LLM** | LLM 在系统提示中已有完整触发条件，规则预筛结果缺乏传递机制，减少代码量（M4 改进） |
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
