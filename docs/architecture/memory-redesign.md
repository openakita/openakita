# OpenAkita 记忆系统重构方案

> 版本: 1.0  
> 日期: 2026-02-20  
> 状态: 设计稿

---

## 目录

1. [现状分析与核心问题](#1-现状分析与核心问题)
2. [设计哲学](#2-设计哲学)
3. [整体架构](#3-整体架构)
4. [数据模型](#4-数据模型)
5. [组件设计](#5-组件设计)
6. [Identity 与 Prompt 编译重构](#6-identity-与-prompt-编译重构)
7. [数据流](#7-数据流)
8. [System Prompt 注入策略](#8-system-prompt-注入策略)
9. [迁移方案](#9-迁移方案)
10. [文件结构](#10-文件结构)

---

## 1. 现状分析与核心问题

### 1.1 当前架构

```
                   ┌─────────────────────────────────┐
                   │        System Prompt             │
                   │  ┌─────────┐  ┌──────────────┐   │
                   │  │ Identity│  │  MEMORY.md   │   │
                   │  │ (编译后) │  │  (800 字符)  │   │
                   │  └─────────┘  └──────────────┘   │
                   │  ┌──────────────────────────────┐│
                   │  │   向量搜索的相关记忆 (可选)    ││
                   │  └──────────────────────────────┘│
                   └──────────────────┬──────────────┘
                                      │
                   ┌──────────────────▼──────────────┐
                   │      Session.messages            │
                   │   (当前对话上下文, 满了就压缩)    │
                   └──────────────────┬──────────────┘
                                      │ 异步提取 (可能失败)
                   ┌──────────────────▼──────────────┐
                   │         memories.json            │
                   │     (全量 JSON, 碎片化 facts)    │
                   │              +                    │
                   │     ChromaDB (向量索引)           │
                   └──────────────────┬──────────────┘
                                      │ 凌晨 3:00
                   ┌──────────────────▼──────────────┐
                   │     MEMORY.md (每日刷新)         │
                   │     conversation_history/        │
                   │     (JSONL 原文, 无索引)          │
                   └─────────────────────────────────┘
```

### 1.2 核心问题清单

| # | 问题 | 严重性 | 说明 |
|---|------|--------|------|
| 1 | **上下文压缩时信息丢失** | 致命 | `context_manager` 压缩消息时不触发记忆提取，被压缩的内容如果之前异步提取失败了就永久丢失 |
| 2 | **没有情节记忆** | 严重 | 只有碎片化的 facts（"用户偏好 JWT"），不保留完整故事（"为什么选 JWT、讨论过程、最终决策"） |
| 3 | **没有工作记忆/思维草稿本** | 严重 | 跨 session 项目靠 MEMORY.md 的 800 字符恢复上下文，远远不够 |
| 4 | **记忆只能追加不能更新** | 中等 | 用户昨天说 "Python 3.10"，今天说 "升级到 3.12"，系统存两条而非更新 |
| 5 | **去重 O(n²)** | 中等 | `daily_consolidator._cleanup_duplicate_memories()` 对所有记忆两两比较，每对调用 LLM |
| 6 | **向量库初始化期间搜索静默失败** | ~~中等~~ 已解决 | FTS5 作为默认搜索后端，零初始化延迟；ChromaDB 降级为可选后端 |
| 7 | **异步提取无重试、无保底** | 中等 | `record_turn()` 里的 `_extract_and_add()` 失败时只 log warning，无重试队列 |
| 8 | **对话原文存了但没被有效利用** | 中等 | `conversation_history/*.jsonl` 只在凌晨归纳时读取，平时不参与检索 |
| 9 | **工具调用信息基本被浪费** | 中等 | `extract_from_turn_with_ai()` 只看 `turn.content`，忽略 `tool_calls`/`tool_results` |
| 10 | **MemoryStorage (SQLite) 已实现但未集成** | 低 | `storage.py` 完整实现了 SQLite 存储，但 `MemoryManager` 仍在用 `memories.json` |
| 11 | **USER.md 几乎全是空白占位符** | 低 | 大量 "[待学习]"，没有从语义记忆自动填充 |
| 12 | **Prompt 编译器过于粗暴** | 低 | 靠关键词匹配行提取，导致 HTML 注释残留（`-->`）、关键内容遗漏 |

### 1.3 现有代码资产（可复用）

| 文件 | 可复用程度 | 说明 |
|------|-----------|------|
| `memory/types.py` | **重构** | Memory dataclass 需要扩展字段，新增 Episode/Scratchpad 类型 |
| `memory/vector_store.py` | **可选保留** | ChromaDB 封装完善，降级为可选搜索后端 (ChromaDBBackend 适配) |
| `memory/storage.py` | **升级为主存储** | SQLite 实现完整，需要新增表 |
| `memory/extractor.py` | **重构** | AI 提取逻辑保留，增加工具调用感知和实体提取 |
| `memory/manager.py` | **重构** | 核心协调器，需要重新设计接口 |
| `memory/consolidator.py` | **保留+扩展** | JSONL 原文保存机制保留，新增索引写入 |
| `memory/daily_consolidator.py` | **重构** | 归纳流程需要适配新架构 |
| `core/context_manager.py` | **修改** | 新增压缩前记忆提取钩子 |
| `prompt/compiler.py` | **重构** | 从关键词匹配升级为 LLM 辅助编译 |
| `prompt/builder.py` | **修改** | 新增 Scratchpad 注入层 |
| `prompt/retriever.py` | **重构** | 多路召回 + 重排序 |

---

## 2. 设计哲学

### 2.1 认知科学启发

人类记忆的工作方式：

```
感知输入 → 感觉记忆(毫秒) → 工作记忆(秒~分钟) → 长期记忆(永久)
                                ↑ 注意力选择          ↑ 编码/巩固
                                ↓ 检索                 ↓ 遗忘/衰减
```

OpenAkita 的对应：

| 人类记忆 | OpenAkita 对应 | 实现 |
|---------|---------------|------|
| 感觉记忆 | 当前消息 | 当前 turn 的原始输入 |
| 工作记忆 | Session.messages + Scratchpad | 当前对话上下文 + 持久化思维空间 |
| 情节记忆 | Episode | 完整的交互故事，带因果链 |
| 语义记忆 | SemanticMemory | 实体-属性结构的事实/偏好/规则 |
| 程序性记忆 | Skill patterns | 工具使用模式、成功方案 |
| 感官记忆 | Attachment | 用户发送/agent 生成的文件、图片、视频、语音 |

### 2.2 核心原则

1. **上下文是非可选的**：Session.messages 是 LLM 对话的基础，不能被记忆取代。记忆是对上下文的增强，不是替代。

2. **信息永远不应该无声丢失**：上下文压缩前必须提取记忆；异步提取失败必须有重试或回退。

3. **记忆是故事，不是碎片**：情节记忆保留因果关系和上下文，语义记忆是从情节中提炼的精华。

4. **记忆可以更新，不只是追加**：同一个实体的属性变化应该更新已有记忆，而非创建重复条目。

5. **Identity ≠ Memory**：身份是"我是谁"（静态），记忆是"我知道什么"（动态）。两者需要联动但不混淆。

6. **人格影响表达，不影响存储**：一个事实无论在 jarvis 还是 default 模式下都是同一个事实。人格只影响检索权重和表达方式。

---

## 3. 整体架构

### 3.1 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                     Prompt Injection Layer                        │
│                                                                   │
│  ┌──────────┐ ┌────────────┐ ┌────────────┐ ┌─────────────────┐ │
│  │ Identity │ │  Working   │ │   Core     │ │    Dynamic      │ │
│  │ (compiled)│ │ Scratchpad │ │  Memory    │ │   Retrieved     │ │
│  │ SOUL+AGENT│ │ (~500 tok) │ │ (~400 tok) │ │  Memories       │ │
│  │ +Persona  │ │            │ │ (auto top-K│ │ (向量+情节+时间) │ │
│  └──────────┘ └────────────┘ └────────────┘ └─────────────────┘ │
└──────────────────────────┬───────────────────────────────────────┘
                           │ 检索请求
┌──────────────────────────▼───────────────────────────────────────┐
│                    Memory Retrieval Engine                        │
│                                                                   │
│  多路召回:                                                        │
│    ├── 语义搜索 (SearchBackend: FTS5默认/ChromaDB可选/API可选)    │
│    ├── 情节搜索 (实体/时间/工具名关联)                            │
│    └── 时间衰减 (最近的记忆权重更高)                              │
│                                                                   │
│  重排序: relevance × recency × importance × access_frequency      │
│  上下文感知: 不只看当前消息, 看最近 3~5 轮对话                     │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                      Memory Store (统一)                          │
│                                                                   │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────┐ │
│  │ Semantic      │  │  Episodic     │  │  Working              │ │
│  │ Memory        │  │  Memory       │  │  Scratchpad           │ │
│  │               │  │               │  │                       │ │
│  │ 实体-属性结构  │  │ 完整交互故事   │  │ 跨 session 思维空间   │ │
│  │ 可更新覆盖     │  │ 工具调用链     │  │ agent 自我总结        │ │
│  │ FACT/PREF/    │  │ 因果决策记录   │  │ 当前项目/进展/        │ │
│  │ RULE/SKILL/   │  │ 关联实体       │  │ 下一步/未解决问题     │ │
│  │ ERROR         │  │ 关联语义记忆   │  │                       │ │
│  └───────────────┘  └───────────────┘  └───────────────────────┘ │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────────┐│
│  │ Raw Conversation Store (对话原文, SQLite + JSONL)             ││
│  │ 按 session/时间/角色/工具名 可查询, 30天后老化                 ││
│  └───────────────────────────────────────────────────────────────┘│
│                                                                   │
│  存储层: SQLite (主存储+FTS5) + 可选: ChromaDB/API Embedding     │
│  搜索后端: FTS5(默认,零依赖) / ChromaDB(可选) / API Embedding(可选)│
│  文件: JSONL (向后兼容备份)                                       │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                  Memory Lifecycle Manager                         │
│                                                                   │
│  提取 → 去重/合并 → 存储 → 强化/衰减 → 整合 → 归档               │
│                                                                   │
│  实时提取:                                                        │
│    ├── 用户消息 → AI 提取 (异步, 有重试队列)                      │
│    ├── 上下文压缩前 → 快速规则提取 + 摘要写入情节                  │
│    └── Session 结束 → 完整情节生成 + 草稿本更新                   │
│                                                                   │
│  批量整合 (凌晨 3:00):                                            │
│    ├── 处理未归纳的原文 → 提取遗漏的记忆                          │
│    ├── 聚类去重 (O(n log n), 非 O(n²))                           │
│    ├── 语义记忆合并/更新                                          │
│    ├── 刷新 Core Memory (MEMORY.md)                              │
│    ├── 刷新 USER.md (从语义记忆自动填充)                          │
│    └── 晋升 PERSONA_TRAIT → user_custom.md (保留现有机制)        │
│                                                                   │
│  衰减与遗忘:                                                      │
│    ├── 基于 access_count 和时间的衰减函数                         │
│    ├── 低重要性 + 长期未访问 → 归档 (不删除)                      │
│    └── TRANSIENT → 24h 过期, SHORT_TERM → 按衰减函数             │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 与现有系统的关系

| 层次 | 现有组件 | 重构方向 |
|------|---------|---------|
| Prompt 注入 | `prompt/builder.py`, `prompt/retriever.py` | 新增 Scratchpad 层, 重构检索逻辑 |
| 检索引擎 | `MemoryManager.get_injection_context()` | 拆分为独立的 `RetrievalEngine` |
| 存储 | `memories.json` + `ChromaDB` + `storage.py` | SQLite (主存储+FTS5), SearchBackend 抽象层, 废弃 `memories.json` |
| 生命周期 | `extractor.py` + `consolidator.py` + `daily_consolidator.py` | 统一为 `LifecycleManager` |
| 上下文桥接 | 无 | 新增 `context_manager` ↔ `memory` 联动 |

---

## 4. 数据模型

### 4.1 语义记忆 (SemanticMemory)

从现有 `Memory` dataclass 演化而来，核心变化是引入**实体-属性结构**和**更新机制**。

```python
@dataclass
class SemanticMemory:
    id: str                           # UUID
    type: MemoryType                  # FACT / PREFERENCE / RULE / SKILL / ERROR
    priority: MemoryPriority          # TRANSIENT / SHORT_TERM / LONG_TERM / PERMANENT

    # 实体-属性结构 (新增)
    subject: str                      # 实体主语, e.g. "用户", "项目X", "Python版本"
    predicate: str                    # 属性/关系, e.g. "偏好", "版本", "位于"
    content: str                      # 完整内容描述

    source: str                       # 来源: "turn_ai" / "task_completion" / "episode" / "manual"
    source_episode_id: str | None     # 关联的情节 ID (可追溯)

    tags: list[str]
    importance_score: float           # 0.0 ~ 1.0
    confidence: float                 # 0.0 ~ 1.0 (新增: 置信度, 多次确认会提升)
    access_count: int                 # 访问次数 (检索时递增, 影响衰减)
    decay_rate: float                 # 衰减速率 (0.0=不衰减, 1.0=最快衰减)

    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime        # 最后被检索的时间 (新增)
    superseded_by: str | None         # 被哪条记忆取代 (新增: 支持更新链)
```

**更新机制**：当提取到一条新记忆，如果 `(subject, predicate)` 与已有记忆匹配：
1. SearchBackend 搜索找到相似记忆 (FTS5 关键词匹配 / ChromaDB 向量 / API Embedding)
2. LLM 判断是"更新"还是"新增"
3. 如果是更新：修改已有记忆的 `content`/`updated_at`，旧值记录在 `superseded_by` 链中
4. 如果是新增：正常插入

### 4.2 情节记忆 (Episode)

全新数据结构，保存完整的交互故事。

```python
@dataclass
class Episode:
    id: str                           # UUID
    session_id: str                   # 关联的 session

    # 内容
    summary: str                      # 一段话描述发生了什么 (~200字)
    goal: str                         # 用户的目标/意图
    outcome: str                      # 结果: "success" / "partial" / "failed" / "ongoing"

    # 时间
    started_at: datetime
    ended_at: datetime

    # 动作节点链 (工具交互的结构化记录)
    action_nodes: list[ActionNode]

    # 关联
    entities: list[str]               # 涉及的实体: 文件路径、项目名、概念
    tools_used: list[str]             # 使用的工具名列表
    linked_memory_ids: list[str]      # 从本情节提取的语义记忆 ID
    tags: list[str]

    # 元数据
    importance_score: float
    access_count: int
    source: str                       # "session_end" / "context_compress" / "daily_consolidation"


@dataclass
class ActionNode:
    """情节中的单个动作节点——一次工具调用链"""
    tool_name: str                    # 工具名
    key_params: dict                  # 关键参数 (非完整参数, 只保留有意义的)
    result_summary: str               # 结果摘要 (~100字)
    success: bool                     # 是否成功
    error_message: str | None         # 如果失败, 错误信息
    decision: str | None              # 基于此结果做出的决策 (可选)
    timestamp: datetime
```

**情节生成时机**：
1. **Session 结束时**：从该 session 的对话 turns 生成完整情节
2. **上下文压缩时**：被压缩的消息生成一个"压缩情节"，确保信息不丢失
3. **凌晨归纳时**：从未处理的原文中补充提取

### 4.3 工作记忆草稿本 (Scratchpad)

跨 session 持久化的思维空间，由 agent 在每次 session 结束时自我总结。

```python
@dataclass
class Scratchpad:
    user_id: str                      # 用户 ID (多用户场景)
    updated_at: datetime
    content: str                      # Markdown 格式, ~2000 字符

    # 结构化字段 (从 content 中解析, 便于检索)
    active_projects: list[str]        # 当前活跃项目
    current_focus: str                # 当前关注点
    open_questions: list[str]         # 未解决的问题
    next_steps: list[str]            # 下一步计划
```

**草稿本内容示例**：

```markdown
## 当前项目
- OpenAkita 记忆系统重构: 架构文档已完成, 准备开始编码

## 近期进展
- 2/20: 完成了记忆架构设计, 用户确认了三层记忆模型方案
- 2/19: 讨论了 CLI session 统一化方案

## 未解决的问题
- 向量模型是否需要换成多语言模型?
- 情节记忆的自动摘要质量如何保证?

## 下一步
- 实现 SQLite 存储层迁移
- 实现上下文压缩 → 记忆提取联动
```

### 4.4 文件/媒体记忆 (Attachment)

追踪用户发送和 agent 生成的文件、图片、视频、语音等附件。

**核心场景**：
- 用户发了一张猫的图片 → 记录为 `direction=inbound`, `description="一只橘猫趴在沙发上"`
- agent 生成了一份报告 PDF → 记录为 `direction=outbound`, `description="用户要求的月度销售报告"`
- 用户发了语音消息 → 记录为 `direction=inbound`, `transcription="明天帮我预约下午3点的会议"`
- 用户日后问"上次我发给你的那张猫的照片给我一下" → 通过 FTS5 搜索 `description` 找回附件

```python
class AttachmentDirection(Enum):
    INBOUND = "inbound"    # 用户发送给 agent
    OUTBOUND = "outbound"  # agent 生成/发送给用户

@dataclass
class Attachment:
    id: str
    session_id: str
    episode_id: str

    # 文件基础信息
    filename: str
    original_filename: str
    mime_type: str                     # image/jpeg, video/mp4, application/pdf ...
    file_size: int

    # 存储位置 (至少一个非空)
    local_path: str                    # 本地磁盘路径
    url: str                           # 远程 URL (IM 平台等)

    direction: AttachmentDirection

    # 内容理解 — 由 LLM / OCR / STT 生成
    description: str                   # 图片/视频/文件的自然语言描述
    transcription: str                 # 语音/视频转写文本
    extracted_text: str                # 从文档提取的文本摘要
    tags: list[str]

    linked_memory_ids: list[str]       # 关联的语义记忆 ID
    created_at: datetime
```

**SQLite 表 `attachments`**：

```sql
CREATE TABLE attachments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    episode_id TEXT DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    original_filename TEXT DEFAULT '',
    mime_type TEXT DEFAULT '',
    file_size INTEGER DEFAULT 0,
    local_path TEXT DEFAULT '',
    url TEXT DEFAULT '',
    direction TEXT DEFAULT 'inbound',   -- inbound / outbound
    description TEXT DEFAULT '',
    transcription TEXT DEFAULT '',
    extracted_text TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    linked_memory_ids TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);

-- FTS5 索引: 搜索描述、转写、文件名
CREATE VIRTUAL TABLE attachments_fts USING fts5(
    description, transcription, extracted_text, filename, tags,
    content=attachments, content_rowid=rowid
);
```

**数据流**：
1. **用户发送图片/文件**：`agent.py` → `_record_inbound_attachments()` → `memory_manager.record_attachment()`
2. **agent 生成文件** (write_file 等工具)：`agent.py` → `_extract_outbound_attachments()` → 自动从 tool_calls 中提取文件路径
3. **用户搜索**："给我那张猫图" → `RetrievalEngine._search_attachments()` 通过关键词检测触发 → FTS5 搜索 `attachments_fts`

**图片描述自动填充**：当 LLM 处理包含图片的多模态消息后，agent 可将 LLM 对图片的理解写入 `description` 字段，使后续搜索更精确。

### 4.5 对话原文存储 (ConversationStore)

从现有 JSONL 升级为 SQLite + JSONL 双存储。

**SQLite 表 `conversation_turns`**：

```sql
CREATE TABLE conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    role TEXT NOT NULL,                    -- "user" / "assistant"
    content TEXT,                          -- 文本内容
    tool_calls TEXT,                       -- JSON: [{name, input, id}]
    tool_results TEXT,                     -- JSON: [{tool_use_id, content, is_error}]
    has_tool_calls BOOLEAN DEFAULT FALSE,  -- 快速过滤
    timestamp TEXT NOT NULL,
    token_estimate INTEGER,               -- 预估 token 数
    episode_id TEXT,                       -- 关联的情节 ID (归纳后填入)
    extracted BOOLEAN DEFAULT FALSE,       -- 是否已完成记忆提取

    UNIQUE(session_id, turn_index)
);

CREATE INDEX idx_turns_session ON conversation_turns(session_id);
CREATE INDEX idx_turns_timestamp ON conversation_turns(timestamp);
CREATE INDEX idx_turns_tool ON conversation_turns(has_tool_calls);
CREATE INDEX idx_turns_extracted ON conversation_turns(extracted);
```

**JSONL 原文保留**（向后兼容 + 备份）：现有的 `conversation_history/*.jsonl` 继续写入，但不再作为主查询入口。30 天后自动清理。

### 4.6 SQLite Schema 总览

在现有 `storage.py` 的 `memories` 表基础上新增：

```sql
-- 现有表: memories (语义记忆, 在原 schema 基础上新增字段)
ALTER TABLE memories ADD COLUMN subject TEXT DEFAULT '';
ALTER TABLE memories ADD COLUMN predicate TEXT DEFAULT '';
ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 0.5;
ALTER TABLE memories ADD COLUMN decay_rate REAL DEFAULT 0.1;
ALTER TABLE memories ADD COLUMN last_accessed_at TEXT;
ALTER TABLE memories ADD COLUMN superseded_by TEXT;
ALTER TABLE memories ADD COLUMN source_episode_id TEXT;

-- 新增表: episodes (情节记忆)
CREATE TABLE episodes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    goal TEXT DEFAULT '',
    outcome TEXT DEFAULT 'completed',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    action_nodes TEXT DEFAULT '[]',     -- JSON array of ActionNode
    entities TEXT DEFAULT '[]',         -- JSON array of strings
    tools_used TEXT DEFAULT '[]',       -- JSON array of strings
    linked_memory_ids TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    importance_score REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    source TEXT DEFAULT 'session_end'
);

CREATE INDEX idx_episodes_session ON episodes(session_id);
CREATE INDEX idx_episodes_time ON episodes(started_at);
CREATE INDEX idx_episodes_outcome ON episodes(outcome);

-- 新增表: scratchpad (工作记忆草稿本)
CREATE TABLE scratchpad (
    user_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    active_projects TEXT DEFAULT '[]',
    current_focus TEXT DEFAULT '',
    open_questions TEXT DEFAULT '[]',
    next_steps TEXT DEFAULT '[]',
    updated_at TEXT NOT NULL
);

-- 新增表: conversation_turns (对话原文索引)
-- (schema 见 4.4 节)

-- 新增表: extraction_queue (提取重试队列)
CREATE TABLE extraction_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    tool_calls TEXT,
    tool_results TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    status TEXT DEFAULT 'pending',      -- pending / processing / completed / failed
    created_at TEXT NOT NULL,
    last_attempted_at TEXT
);

-- FTS5 全文搜索虚拟表 (默认搜索后端, 零外部依赖)
-- 配合 jieba 预分词: 写入前先将中文文本分词为空格分隔的 tokens
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, subject, predicate, tags,
    content=memories, content_rowid=rowid,
    tokenize='unicode61'
);

-- 触发器: memories 表变更时自动同步 FTS5 索引
CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
    VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
    VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
    VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
    INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
    VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
END;

-- API Embedding 缓存表 (可选, 使用在线 embedding API 时)
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT PRIMARY KEY,
    embedding BLOB NOT NULL,          -- numpy array 序列化
    model TEXT NOT NULL,              -- e.g. "text-embedding-v3"
    dimensions INTEGER DEFAULT 1024,
    created_at TEXT NOT NULL
);
```

---

## 5. 组件设计

### 5.1 MemoryManager (重构)

从"大杂烩协调器"拆分为明确的子组件：

```python
class MemoryManager:
    """记忆系统入口, 协调各子组件"""

    def __init__(self, ...):
        self.store = UnifiedStore(db_path, vector_store)   # 统一存储
        self.extractor = MemoryExtractor(brain)            # 提取器
        self.retriever = RetrievalEngine(store)            # 检索引擎
        self.lifecycle = LifecycleManager(store, brain)    # 生命周期
        self.scratchpad = ScratchpadManager(store, brain)  # 草稿本管理

    # === 实时路径 (每次对话调用) ===

    async def record_turn(self, role, content, tool_calls, tool_results):
        """记录对话轮次, 触发实时提取"""
        # 1. 写入 SQLite conversation_turns
        # 2. 写入 JSONL (向后兼容)
        # 3. 触发异步提取 (带重试队列)

    async def on_context_compressing(self, messages_to_compress: list[dict]):
        """上下文压缩前的钩子 — 确保信息不丢失"""
        # 1. 快速规则提取关键事实
        # 2. 生成压缩情节 (summary + action_nodes)
        # 3. 将情节写入 episodes 表

    async def on_session_end(self, session_id, conversation_turns):
        """Session 结束时的完整处理"""
        # 1. 生成完整情节 (LLM 摘要)
        # 2. 从情节中提取语义记忆
        # 3. 更新草稿本

    def get_injection_context(self, query, recent_messages=None) -> str:
        """获取注入到 system prompt 的记忆上下文"""
        # 委托给 RetrievalEngine

    # === 批量路径 (凌晨执行) ===

    async def consolidate_daily(self):
        """每日归纳"""
        # 委托给 LifecycleManager
```

### 5.2 UnifiedStore (新建)

统一 SQLite + ChromaDB 的读写，取代 `memories.json`。

```python
class UnifiedStore:
    """统一存储层, SQLite 为主存储, SearchBackend 为搜索引擎"""

    def __init__(self, db_path: Path, search_backend: SearchBackend):
        self.db = MemoryStorage(db_path)       # 复用现有 storage.py
        self.search = search_backend           # 可插拔搜索后端

    # --- 语义记忆 CRUD ---
    def save_semantic(self, memory: SemanticMemory) -> str: ...
    def update_semantic(self, memory_id: str, updates: dict) -> bool: ...
    def find_similar_semantic(self, subject: str, predicate: str) -> SemanticMemory | None: ...
    def search_semantic(self, query: str, limit: int) -> list[SemanticMemory]: ...

    # --- 情节记忆 CRUD ---
    def save_episode(self, episode: Episode) -> str: ...
    def search_episodes(self, query: str, limit: int) -> list[Episode]: ...
    def get_episodes_by_entity(self, entity: str) -> list[Episode]: ...
    def get_recent_episodes(self, days: int = 7) -> list[Episode]: ...

    # --- 草稿本 ---
    def get_scratchpad(self, user_id: str) -> Scratchpad | None: ...
    def save_scratchpad(self, scratchpad: Scratchpad): ...

    # --- 对话原文 ---
    def save_turn(self, session_id: str, turn_index: int, ...): ...
    def get_unextracted_turns(self) -> list[dict]: ...
    def mark_turns_extracted(self, session_id: str, turn_indices: list[int]): ...

    # --- 提取队列 ---
    def enqueue_extraction(self, ...): ...
    def dequeue_extraction(self, batch_size: int = 10) -> list[dict]: ...
```

### 5.3 SearchBackend (新建)

可插拔搜索后端抽象，三种实现按需选择：

```python
class SearchBackend(Protocol):
    """搜索后端抽象接口"""
    def search(self, query: str, limit: int = 10,
               filter_type: str | None = None) -> list[tuple[str, float]]: ...
    def add(self, memory_id: str, content: str, metadata: dict) -> bool: ...
    def delete(self, memory_id: str) -> bool: ...
    def batch_add(self, items: list[dict]) -> int: ...
    @property
    def available(self) -> bool: ...
    @property
    def backend_type(self) -> str: ...  # "fts5" / "chromadb" / "api_embedding"


class FTS5Backend(SearchBackend):
    """SQLite FTS5 全文搜索 (默认, 零外部依赖)

    - jieba 分词: 写入前将中文文本分词为空格分隔 tokens
    - BM25 排序: SQLite FTS5 内置 bm25() 函数
    - 零初始化延迟, 无模型下载
    """
    backend_type = "fts5"


class ChromaDBBackend(SearchBackend):
    """ChromaDB 向量搜索 (可选, 封装现有 VectorStore)

    - 复用 vector_store.py 的全部逻辑
    - 本地 embedding 模型 (text2vec-base-chinese)
    - 后台初始化, 不可用时自动降级到 FTS5
    """
    backend_type = "chromadb"


class APIEmbeddingBackend(SearchBackend):
    """在线 Embedding API (可选, DashScope/OpenAI)

    - 调用 DashScope text-embedding-v3 或 OpenAI text-embedding-3-small
    - embedding 结果缓存到 SQLite embedding_cache 表
    - 余弦相似度排序
    - 质量最高, 需要网络和 API key
    """
    backend_type = "api_embedding"


def create_search_backend(backend_type: str = "fts5", **kwargs) -> SearchBackend:
    """工厂函数: 根据配置创建搜索后端"""
    ...
```

**降级策略**: ChromaDB/API 后端不可用时 → 自动回退到 FTS5Backend。FTS5 始终可用 (SQLite 内置)。

### 5.4 MemoryExtractor (重构)

从现有 `extractor.py` 演化，核心变化：

1. **工具调用感知**：提取 prompt 包含 `tool_calls` 和 `tool_results` 的结构化摘要
2. **实体-属性提取**：输出 `subject + predicate + content` 而非单纯的 `content`
3. **更新检测**：判断是新增还是更新已有记忆
4. **重试队列**：失败时入队，后续批量重试

```python
class MemoryExtractor:
    """AI 驱动的记忆提取器"""

    EXTRACTION_PROMPT_V2 = """分析这轮对话，提取值得记住的信息。

对话内容:
[{role}]: {content}
{tool_context}

对于每条值得记录的信息，用 JSON 输出:
[
  {{
    "type": "FACT|PREFERENCE|RULE|SKILL|ERROR",
    "subject": "实体主语 (谁/什么)",
    "predicate": "属性/关系 (偏好/版本/位于/...)",
    "content": "完整描述",
    "importance": 0.5-1.0,
    "is_update": false,
    "update_hint": "如果是对已知事实的更新, 描述哪个事实被更新了"
  }}
]

如果没有值得记录的信息, 只输出: NONE"""

    async def extract_from_turn_v2(self, turn: ConversationTurn) -> list[dict]:
        """v2 提取: 感知工具调用, 输出实体-属性结构"""
        # 构建包含工具上下文的 prompt
        tool_context = self._build_tool_context(turn.tool_calls, turn.tool_results)
        # 调用 LLM
        # 解析结果, 返回结构化记忆候选

    async def generate_episode(self, turns: list[ConversationTurn],
                                session_id: str) -> Episode:
        """从对话轮次生成情节记忆"""
        # 1. 提取 action_nodes (工具调用链)
        # 2. LLM 生成 summary + goal + outcome
        # 3. 提取 entities (文件路径, 项目名, 概念)
        # 4. 组装 Episode 对象

    async def update_scratchpad(self, current: Scratchpad | None,
                                 episode: Episode) -> Scratchpad:
        """基于最新情节更新草稿本"""
        # LLM 生成: "基于这个情节, 更新我的工作记忆"
```

### 5.5 RetrievalEngine (新建)

从 `get_injection_context()` 和 `retriever.py` 拆出，实现多路召回 + 重排序。

```python
class RetrievalEngine:
    """多路召回 + 重排序的记忆检索引擎"""

    def retrieve(self, query: str, recent_messages: list[dict] = None,
                 active_persona: str = None, max_tokens: int = 700) -> str:
        """
        检索并格式化要注入的记忆上下文

        Args:
            query: 主查询 (通常是用户最新消息)
            recent_messages: 最近 3~5 轮消息 (增强检索上下文)
            active_persona: 当前激活人格 (影响检索权重)
            max_tokens: 最大 token 预算
        """
        # 1. 构建增强查询 (当前消息 + 最近上下文摘要)
        enhanced_query = self._build_enhanced_query(query, recent_messages)

        # 2. 多路召回
        semantic_results = self._search_semantic(enhanced_query)
        episode_results = self._search_episodes(enhanced_query)
        recent_results = self._search_recent(days=3)

        # 3. 去重 + 合并
        candidates = self._merge_and_deduplicate(
            semantic_results, episode_results, recent_results
        )

        # 4. 重排序
        ranked = self._rerank(candidates, query, active_persona)

        # 5. 格式化输出 (在 token 预算内)
        return self._format_within_budget(ranked, max_tokens)

    def _rerank(self, candidates, query, persona):
        """综合排序: relevance × recency × importance × access_freq"""
        for c in candidates:
            c.score = (
                c.relevance * 0.4
                + c.recency_score * 0.25
                + c.importance_score * 0.2
                + c.access_frequency_score * 0.15
            )
            # 人格加权 (可选)
            if persona == "tech_expert" and c.type in ("SKILL", "ERROR"):
                c.score *= 1.2
        return sorted(candidates, key=lambda c: c.score, reverse=True)
```

### 5.6 上下文-记忆桥接 (ContextManager 修改)

在现有 `context_manager.py` 的 `compress_if_needed()` 中插入钩子：

```python
# context_manager.py 修改点

async def compress_if_needed(self, messages, *, memory_manager=None, **kwargs):
    # ... 现有的 token 估算和阈值检查 ...

    if current_tokens > soft_limit:
        # >>> 新增: 压缩前记忆提取 <<<
        if memory_manager:
            early_messages = [msg for group in early_groups for msg in group]
            await memory_manager.on_context_compressing(early_messages)

        # ... 现有的 LLM 压缩逻辑 ...
```

`on_context_compressing` 的实现采用**双保险策略**：

```python
async def on_context_compressing(self, messages_to_compress: list[dict]):
    """双保险: 快速规则提取 + 压缩摘要写入情节"""

    # 路径 1: 快速规则提取 (低延迟, ~10ms)
    # 扫描即将被压缩的消息, 用正则/关键词提取关键事实
    quick_facts = self.extractor.extract_quick_facts(messages_to_compress)
    for fact in quick_facts:
        self.store.save_semantic(fact)

    # 路径 2: 生成压缩情节 (用已有的压缩摘要, 不额外调 LLM)
    # 压缩摘要由 context_manager 生成后回调写入
    # 这样即使路径 1 遗漏了信息, 情节摘要中也保留了

    # 标记这些 turns 为已提取
    for msg in messages_to_compress:
        session_id = msg.get("_session_id")
        turn_index = msg.get("_turn_index")
        if session_id and turn_index is not None:
            self.store.mark_turns_extracted(session_id, [turn_index])
```

### 5.7 去重优化 (O(n log n))

替代现有的 O(n²) 两两 LLM 比较：

```python
async def deduplicate_batch(self, memories: list[SemanticMemory]) -> int:
    """基于聚类的批量去重"""

    # 1. 按 type 分组
    by_type = group_by(memories, key=lambda m: m.type)
    deleted = 0

    for mem_type, group in by_type.items():
        if len(group) < 2:
            continue

        # 2. 用 SearchBackend 做聚类 (FTS5: 关键词重叠 / ChromaDB: 向量距离)
        clusters = self._cluster_by_similarity(group, threshold=0.15)

        # 3. 每个聚类内部用 LLM 判断 (聚类通常很小, 2~5 条)
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            keep, remove = await self._pick_best_in_cluster(cluster)
            for mem in remove:
                self.store.delete_semantic(mem.id)
                deleted += 1

    return deleted
```

---

## 6. Identity 与 Prompt 编译重构

### 6.1 当前编译器的问题

现有 `prompt/compiler.py` 使用**关键词行匹配**提取内容：

```python
# 现有逻辑 (compiler.py line 126-148)
for line in lines:
    if stripped.startswith(("-", "*", "1.", "2.", "3.")):
        if len(stripped) < 100:
            principles.append(stripped)
```

问题：
- `-->` HTML 注释残留（因为匹配到了 `-->`开头的行）
- 长句被丢弃（`< 100` 字符限制），但有些核心原则就是长句
- 叙述性段落中的关键信息被遗漏
- 不理解语义，只做字符串匹配

### 6.2 新编译方案: LLM 辅助 + 缓存

**核心思路**：首次编译用 LLM 生成高质量摘要，结果缓存到 `compiled/` 目录。只有源文件变更时才重新编译。

```python
# prompt/compiler.py 重构

class PromptCompiler:
    """LLM 辅助的 Prompt 编译器"""

    COMPILE_PROMPTS = {
        "soul": {
            "system": "你是一个文本精简专家。",
            "user": """将以下 AI 身份文档精简为核心原则摘要。

要求:
- 保留所有核心价值观和行为原则
- 保留关键的"做/不做"清单
- 删除叙事性段落、示例和比喻
- 删除 HTML 注释和格式噪声
- 输出纯 Markdown，不超过 {max_tokens} tokens
- 使用紧凑的列表格式

原文:
{content}""",
            "max_tokens": 150,
        },

        "agent_core": {
            "system": "你是一个文本精简专家。",
            "user": """将以下 AI 行为规范文档精简为核心执行原则。

要求:
- 保留 Ralph Wiggum 核心循环逻辑
- 保留任务执行流程
- 保留禁止行为清单
- 删除配置示例、命令列表、架构说明等参考信息
- 输出纯 Markdown，不超过 {max_tokens} tokens

原文:
{content}""",
            "max_tokens": 250,
        },

        "agent_tooling": {
            "system": "你是一个文本精简专家。",
            "user": """从以下文档中提取工具使用原则。

要求:
- 保留工具选择优先级
- 保留禁止的敷衍响应模式
- 保留渐进式披露机制说明
- 删除具体工具列表（运行时通过 tools 参数注入）
- 输出纯 Markdown，不超过 {max_tokens} tokens

原文:
{content}""",
            "max_tokens": 200,
        },

        "user": {
            "system": "你是一个文本精简专家。",
            "user": """从以下用户档案中提取已知信息。

要求:
- 只保留有实际内容的字段（跳过"[待学习]"等占位符）
- 保留用户称呼、技术栈、偏好、工作习惯等已知信息
- 输出紧凑的列表格式，不超过 {max_tokens} tokens
- 如果没有任何已知信息，输出空字符串

原文:
{content}""",
            "max_tokens": 120,
        },
    }

    def __init__(self, brain=None):
        self.brain = brain

    async def compile_all(self, identity_dir: Path) -> dict[str, Path]:
        """编译所有 identity 文件"""
        compiled_dir = identity_dir / "compiled"
        compiled_dir.mkdir(exist_ok=True)
        results = {}

        for target, config in self.COMPILE_PROMPTS.items():
            source_path = self._get_source_path(identity_dir, target)
            if not source_path or not source_path.exists():
                continue

            output_path = compiled_dir / f"{target.replace('_', '.')}.md"

            # 检查是否需要重新编译 (源文件是否变更)
            if self._is_up_to_date(source_path, output_path):
                results[target] = output_path
                continue

            # LLM 编译
            source_content = source_path.read_text(encoding="utf-8")
            compiled = await self._compile_with_llm(source_content, config)

            # 写入编译产物
            output_path.write_text(compiled, encoding="utf-8")
            results[target] = output_path

        # 更新时间戳
        (compiled_dir / ".compiled_at").write_text(
            datetime.now().isoformat(), encoding="utf-8"
        )
        return results

    async def _compile_with_llm(self, content: str, config: dict) -> str:
        """使用 LLM 编译"""
        if not self.brain:
            # LLM 不可用时回退到规则编译 (保留现有逻辑作为降级)
            return self._compile_with_rules(content, config)

        prompt = config["user"].format(
            content=content, max_tokens=config["max_tokens"]
        )
        response = await self.brain.think_lightweight(
            prompt, system=config["system"]
        )
        result = response.content if hasattr(response, 'content') else str(response)
        return result.strip()

    def _is_up_to_date(self, source: Path, output: Path) -> bool:
        """检查编译产物是否比源文件新"""
        if not output.exists():
            return False
        return output.stat().st_mtime > source.stat().st_mtime

    def _compile_with_rules(self, content: str, config: dict) -> str:
        """规则编译降级 (保留现有逻辑, 清理 HTML 残留)"""
        # ... 保留现有的关键词匹配逻辑, 但增加:
        # 1. 清理 HTML 注释 (<!-- --> 和 -->)
        # 2. 清理空行和格式噪声
        import re
        content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
        content = re.sub(r'^\s*-->\s*$', '', content, flags=re.MULTILINE)
        # ... 继续现有提取逻辑 ...
```

### 6.3 USER.md 自动填充

USER.md 从"空白占位符模板"变为"从语义记忆自动填充"：

```python
async def refresh_user_md(self, identity_dir: Path):
    """从语义记忆自动填充 USER.md"""

    # 查询用户相关的语义记忆
    user_facts = self.store.search_semantic(
        query="用户", types=["FACT", "PREFERENCE"], limit=30
    )

    # 按类别分组
    categories = {
        "basic_info": [],      # 称呼、身份、时区
        "tech_stack": [],      # 编程语言、框架、工具
        "dev_env": [],         # OS、IDE、Shell
        "preferences": [],     # 沟通风格、代码风格
        "projects": [],        # 活跃项目
    }

    # LLM 分类 (或基于 subject/predicate 规则分类)
    for mem in user_facts:
        category = self._categorize_user_fact(mem)
        if category in categories:
            categories[category].append(mem.content)

    # 生成 USER.md
    user_md = self._render_user_md(categories)
    user_path = identity_dir / "USER.md"
    user_path.write_text(user_md, encoding="utf-8")

    # 触发重编译
    await self.compiler.compile_all(identity_dir)
```

### 6.4 Persona 与记忆的集成点

人格系统本身不改动，但在以下位置与记忆系统联动：

1. **检索时**：`RetrievalEngine.retrieve()` 接受 `active_persona` 参数，影响结果排序权重
2. **草稿本生成时**：如果当前是 jarvis 模式，草稿本的"agent 自我总结"可以带 jarvis 风格（通过在 prompt 中注入人格提示）
3. **PERSONA_TRAIT 晋升**：保留现有的 `daily_consolidator._promote_persona_traits_to_identity()` 机制

---

## 7. 数据流

### 7.1 实时路径 (每次对话)

```
用户消息到达
    │
    ├──→ Session.messages (当前对话上下文, 全保真)
    │
    ├──→ MemoryManager.record_turn()
    │       ├── 写入 SQLite conversation_turns
    │       ├── 写入 JSONL (向后兼容)
    │       └── 异步提取 (有重试队列)
    │              ├── 成功 → 语义记忆入库
    │              └── 失败 → extraction_queue 入队
    │
    ├──→ System Prompt 构建
    │       ├── Identity (编译后, 缓存)
    │       ├── Scratchpad (从 SQLite 读取)
    │       ├── Core Memory (从 MEMORY.md 读取)
    │       └── Dynamic Memories (RetrievalEngine 检索)
    │              ├── 语义搜索: SearchBackend (FTS5/ChromaDB/API)
    │              ├── 情节搜索: 相关实体/工具
    │              └── 重排序 → 格式化 → 注入
    │
    └──→ LLM 推理 → 工具调用 → 结果 → 回复
            │
            └──→ MemoryManager.record_turn("assistant", ...)
                    └── 包含 tool_calls + tool_results
```

### 7.2 上下文压缩路径

```
上下文接近 token 上限
    │
    ├── context_manager.compress_if_needed()
    │       │
    │       ├── 识别 early_messages (将被压缩的部分)
    │       │
    │       ├──→ memory_manager.on_context_compressing(early_messages)
    │       │       │
    │       │       ├── 路径 1: 快速规则提取 → 语义记忆
    │       │       │   (正则/关键词, ~10ms, 不调 LLM)
    │       │       │
    │       │       └── 路径 2: 生成压缩情节
    │       │           (摘要由后续 LLM 压缩生成后回填)
    │       │
    │       ├── LLM 压缩 early_messages → summary
    │       │
    │       ├──→ 将 summary 回填到压缩情节
    │       │
    │       └── 注入 summary 到 recent_messages 头部
    │
    └── 继续对话 (压缩后的 messages)
```

### 7.3 Session 结束路径

```
Session 结束 / 用户退出
    │
    ├── MemoryManager.on_session_end()
    │       │
    │       ├── 1. 生成完整 Episode
    │       │       ├── LLM 摘要: summary + goal + outcome
    │       │       ├── 提取 ActionNodes (工具调用链)
    │       │       ├── 提取 entities (文件、项目、概念)
    │       │       └── 写入 SQLite episodes 表
    │       │
    │       ├── 2. 从 Episode 提取语义记忆
    │       │       ├── LLM 提取 facts/preferences/rules
    │       │       ├── 更新检测: 新增 or 更新已有
    │       │       └── 写入 SQLite memories 表 + SearchBackend 索引
    │       │
    │       ├── 3. 更新 Scratchpad
    │       │       ├── LLM: "基于这个情节, 更新我的工作记忆"
    │       │       ├── 输入: 当前草稿本 + 新情节
    │       │       └── 输出: 更新后的草稿本 → SQLite
    │       │
    │       └── 4. 处理提取重试队列
    │               └── extraction_queue 中 pending 的项目
    │
    └── 清理 Session 状态
```

### 7.4 凌晨归纳路径

```
凌晨 3:00 定时任务
    │
    ├── 1. 处理未归纳的原文
    │       ├── 查询 conversation_turns WHERE extracted = FALSE
    │       ├── 按 session 分组
    │       ├── 对每个 session 生成 Episode (如果还没有)
    │       └── 从 Episode 提取语义记忆
    │
    ├── 2. 批量去重 (O(n log n))
    │       ├── 按 type 分组
    │       ├── SearchBackend 聚类 (阈值 0.15)
    │       └── 聚类内 LLM 判断 → 保留最优 / 合并
    │
    ├── 3. 衰减计算
    │       ├── 对所有 SHORT_TERM 记忆计算衰减
    │       │   score = importance * (1 - decay_rate) ^ days_since_access
    │       ├── score < 0.1 且 access_count < 2 → 归档
    │       └── 清理 TRANSIENT 记忆 (>24h)
    │
    ├── 4. 刷新 MEMORY.md
    │       ├── 从语义记忆选取 top-K (按 importance × recency)
    │       ├── 每类最多 5 条, 总计 ~1500 字符
    │       └── 写入 identity/MEMORY.md
    │
    ├── 5. 刷新 USER.md
    │       ├── 从语义记忆中提取用户相关 facts
    │       ├── 按类别组织 (基本信息/技术栈/偏好/项目)
    │       └── 写入 identity/USER.md
    │
    ├── 6. 晋升 PERSONA_TRAIT (保留现有机制)
    │       └── 高置信度特质 → identity/personas/user_custom.md
    │
    ├── 7. 触发 Identity 重编译
    │       └── compiler.compile_all() (LLM 辅助)
    │
    └── 8. 清理
            ├── 过期 JSONL 文件 (>30 天)
            └── 保存归纳报告
```

---

## 8. System Prompt 注入策略

### 8.1 注入层次 (重构后)

```
System Prompt 总预算: ~3000 tokens (可配置)

┌─ System Section ────────────────────────────────┐
│                                                   │
│  1. Identity (~350 tokens)                       │
│     ├── soul.summary    (~100 tok, LLM 编译)     │
│     ├── agent.core      (~120 tok, LLM 编译)     │
│     ├── agent.tooling   (~130 tok, LLM 编译)     │
│     └── (首次编译后缓存, 源文件变更时重编译)      │
│                                                   │
│  2. Persona Overlay (~200 tokens, 如果激活)       │
│     ├── 预设人格 prompt 片段                      │
│     └── user_custom.md (自动归集的偏好)           │
│                                                   │
│  3. Runtime (~150 tokens)                        │
│     └── OS/CWD/时间/工具状态/Shell 类型           │
│                                                   │
├─ Developer Section ─────────────────────────────┤
│                                                   │
│  4. Session Rules (~300 tokens)                  │
│     └── CLI/IM 规则 + 消息分型 + ask_user 规则   │
│                                                   │
│  5. Working Scratchpad (~500 tokens) ← 新增      │
│     └── 当前项目/进展/下一步/未解决问题            │
│     └── 每次 session 结束时 agent 自我更新        │
│                                                   │
│  6. Core Memory (~400 tokens) ← 取代 MEMORY.md  │
│     └── 语义记忆 top-K 精选 (事实/偏好/规则)     │
│     └── ~1500 字符 (原来 800, 扩大近一倍)        │
│                                                   │
│  7. Dynamic Memories (~300 tokens)               │
│     └── 基于当前话题检索的相关记忆 + 情节          │
│     └── 多路召回 + 重排序, 上下文感知              │
│                                                   │
├─ User Section ──────────────────────────────────┤
│                                                   │
│  8. User Profile (~120 tokens)                   │
│     └── user.summary (从语义记忆自动填充)         │
│     └── persona.custom (PERSONA_TRAIT 晋升)       │
│                                                   │
├─ Tool Section ──────────────────────────────────┤
│                                                   │
│  9. Catalogs (预算独立, ~800 tokens)             │
│     └── Tools + Skills + MCP 清单                 │
│                                                   │
└──────────────────────────────────────────────────┘
```

### 8.2 与现有 builder.py 的对应关系

| 现有层 | 重构后 | 变化 |
|--------|--------|------|
| `_build_identity_section()` | Identity | LLM 编译替代关键词匹配 |
| `_build_persona_section()` | Persona Overlay | 不变 |
| `_build_runtime_section()` | Runtime | 不变 |
| `_build_session_type_rules()` | Session Rules | 不变 |
| `_build_memory_section()` | **拆分为三层** | Scratchpad + Core Memory + Dynamic |
| `_build_user_section()` | User Profile | 从记忆自动填充 |
| `_build_catalogs_section()` | Catalogs | 不变 |

---

## 9. 迁移方案

### 9.1 数据迁移

```
Phase 1: 存储迁移
├── 1. 初始化新 SQLite 表 (memories 新增字段 + episodes + scratchpad + conversation_turns)
├── 2. 从 memories.json 导入到 SQLite (复用 storage.py 的 import_from_json)
├── 3. 新字段 (subject, predicate, confidence 等) 设默认值, 凌晨归纳时逐步填充
└── 4. memories.json 保留为备份, 不再作为主存储

Phase 2: 对话原文迁移
├── 1. 新的 record_turn() 同时写 SQLite + JSONL
└── 2. 历史 JSONL 保持不动, 凌晨归纳时可选择性索引

Phase 3: 编译器迁移
├── 1. 新编译器首次运行时用 LLM 重新编译所有 identity 文件
├── 2. 编译结果缓存到 compiled/ 目录
└── 3. 旧编译器的规则逻辑保留为 LLM 不可用时的降级方案

Phase 4: 搜索后端迁移
├── 1. FTS5 为默认后端, jieba 新增到 requirements.txt
├── 2. 现有 ChromaDB 逻辑封装为 ChromaDBBackend (可选)
├── 3. 新增 APIEmbeddingBackend (可选, 需配置 API key)
└── 4. config.py 新增配置项: search_backend, embedding_api_provider, embedding_api_key, embedding_api_model
```

### 9.2 代码变更清单

| 文件 | 变更类型 | 描述 |
|------|---------|------|
| `memory/types.py` | 重构 | 新增 Episode, ActionNode, Scratchpad dataclass; 扩展 Memory 字段 |
| `memory/storage.py` | 扩展 | 新增 episodes/scratchpad/conversation_turns/extraction_queue 表 |
| `memory/manager.py` | 重构 | 拆分为 MemoryManager + UnifiedStore + RetrievalEngine + LifecycleManager |
| `memory/extractor.py` | 重构 | v2 提取 prompt (工具感知, 实体-属性); Episode 生成; Scratchpad 更新 |
| `memory/retrieval.py` | **新建** | 多路召回 + 重排序引擎 |
| `memory/unified_store.py` | **新建** | 统一 SQLite + SearchBackend 读写 |
| `memory/search_backends.py` | **新建** | SearchBackend 抽象 + FTS5/ChromaDB/API 三种实现 |
| `memory/lifecycle.py` | **新建** | 凌晨归纳 + 衰减 + 去重 + 刷新 |
| `memory/vector_store.py` | 封装适配 | 降级为可选后端, 由 ChromaDBBackend 封装 |
| `memory/consolidator.py` | 修改 | 新增 SQLite 写入, 保留 JSONL |
| `memory/daily_consolidator.py` | 重构 | 适配新架构, 新增 USER.md 刷新 |
| `core/context_manager.py` | 修改 | 新增 `on_context_compressing` 钩子 |
| `core/agent.py` | 修改 | 调用新 MemoryManager 接口 |
| `prompt/compiler.py` | 重构 | LLM 辅助编译 + 源文件变更检测 |
| `prompt/builder.py` | 修改 | 新增 Scratchpad 注入层, 调整 Memory 层 |
| `prompt/retriever.py` | 重构 | 委托给 RetrievalEngine |
| `sessions/session.py` | 微调 | `_truncate_history` 触发记忆提取 |

### 9.3 兼容性保证

1. **memories.json 不删除**：迁移后保留为备份，新系统不再读写
2. **JSONL 原文继续写入**：双写期间，SQLite 和 JSONL 同步
3. **现有 API 不变**：`add_memory`, `search_memory`, `get_memory_stats` 工具接口保持兼容
4. **编译降级**：LLM 不可用时自动回退到规则编译
5. **搜索后端降级**：FTS5 为默认 (零依赖), ChromaDB/API Embedding 为可选增强; 可选后端不可用时自动回退到 FTS5

---

## 10. 文件结构

### 10.1 重构后的 memory 模块

```
src/openakita/memory/
├── __init__.py                 # 导出公共接口
├── types.py                    # 数据类型: SemanticMemory, Episode, ActionNode, Scratchpad
├── manager.py                  # MemoryManager: 入口协调器
├── unified_store.py            # UnifiedStore: SQLite + SearchBackend 统一存储
├── storage.py                  # MemoryStorage: SQLite 实现 (扩展, 含 FTS5)
├── search_backends.py          # SearchBackend: FTS5(默认)/ChromaDB(可选)/API(可选) (新建)
├── vector_store.py             # VectorStore: ChromaDB 实现 (可选, 由 ChromaDBBackend 封装)
├── extractor.py                # MemoryExtractor: AI 提取 (重构)
├── retrieval.py                # RetrievalEngine: 多路召回 + 重排序 (新建)
├── lifecycle.py                # LifecycleManager: 归纳 + 衰减 + 去重 (新建)
├── consolidator.py             # MemoryConsolidator: 原文保存 (保留)
├── daily_consolidator.py       # DailyConsolidator: 凌晨任务 (重构)
└── model_hub.py                # Embedding 模型管理 (仅 ChromaDB 后端需要)
```

### 10.2 数据目录

```
data/
├── memory/
│   ├── openakita.db              # SQLite 主数据库 (memories + FTS5 + episodes + scratchpad + turns + embedding_cache)
│   ├── chromadb/                  # 可选: ChromaDB 向量索引 (仅 chromadb 后端启用时)
│   ├── conversation_history/      # JSONL 原文 (向后兼容, 30天清理)
│   └── daily_summaries/           # 每日归纳报告
├── sessions/
│   ├── sessions.json              # Session 持久化
│   └── channel_registry.json      # 通道注册
└── runtime_state.json             # 运行时状态

identity/
├── SOUL.md                        # 核心哲学 (手动维护)
├── AGENT.md                       # 行为规范 (手动维护)
├── USER.md                        # 用户档案 (凌晨自动刷新 + 手动编辑)
├── MEMORY.md                      # 核心记忆精选 (凌晨自动刷新, ~1500字符)
├── compiled/                      # 编译产物 (LLM 辅助生成, 缓存)
│   ├── .compiled_at
│   ├── soul.summary.md
│   ├── agent.core.md
│   ├── agent.tooling.md
│   ├── user.summary.md
│   └── persona.custom.md
├── personas/                      # 人格预设 (不变)
│   ├── default.md
│   ├── jarvis.md
│   ├── girlfriend.md
│   ├── ...
│   └── user_custom.md             # 自动晋升 (不变)
└── prompts/
    └── policies.md                # 策略文件 (不变)
```

---

## 附录 A: 关键设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 主存储 | JSON / SQLite / PostgreSQL | SQLite | 已有实现 (`storage.py`)，无外部依赖，性能足够 |
| 默认搜索 | FTS5 / ChromaDB / Faiss | FTS5 (SQLite 内置) | 零依赖、零初始化延迟、BM25 对中文记忆够用 |
| 可选搜索增强 | ChromaDB / API Embedding | 两者均支持 | ChromaDB 适合离线; API Embedding (DashScope/OpenAI) 质量最高 |
| 情节生成时机 | 实时 / Session结束 / 凌晨 | Session结束 + 压缩时 + 凌晨补充 | 平衡实时性和开销 |
| 草稿本作者 | 用户编辑 / Agent自动 / 混合 | Agent 自动 (用户可手动覆盖) | 减少用户负担，Agent 最了解自己的状态 |
| 编译方式 | 规则 / LLM / 混合 | LLM 为主, 规则降级 | 质量远胜规则匹配，缓存后无额外开销 |
| 去重算法 | O(n²) LLM / 聚类 + LLM | 聚类 + 聚类内 LLM | O(n log n)，LLM 调用量大幅减少 |
| 原文存储 | 不保留 / JSONL / SQLite | SQLite + JSONL双写 | SQLite 可查询, JSONL 向后兼容 |
| MEMORY.md 容量 | 800字符 / 1500字符 / 无限 | ~1500 字符 | 平衡信息量和 token 成本 |

## 附录 B: 性能与成本估算

| 操作 | 频率 | LLM 调用 | 预估延迟 |
|------|------|---------|---------|
| 实时记忆提取 | 每条用户消息 | 1次 (lightweight) | ~500ms (异步) |
| 上下文压缩前提取 | 上下文满时 | 0次 (规则提取) | ~10ms |
| Session 结束情节生成 | 每个 session | 1~2次 | ~2s (异步) |
| Session 结束草稿本更新 | 每个 session | 1次 | ~1s (异步) |
| 凌晨去重 | 每天 1次 | ~N/5 次 (聚类内) | ~30s/100条记忆 |
| 凌晨 MEMORY.md 刷新 | 每天 1次 | 0~1次 | ~1s |
| 凌晨 USER.md 刷新 | 每天 1次 | 0~1次 | ~1s |
| Identity 编译 | 源文件变更时 | 4次 | ~5s (缓存后 0) |

### 搜索后端性能对比

| 指标 | FTS5 (默认) | ChromaDB (可选) | API Embedding (可选) |
|------|------------|----------------|---------------------|
| 初始化时间 | 0ms | 10~60s (模型下载) | 0ms |
| 额外依赖大小 | ~15MB (jieba) | ~500MB | ~0 (httpx 已有) |
| 搜索延迟 | <5ms | ~50ms | ~200ms (含网络) |
| 搜索质量 | 关键词匹配 (BM25) | 本地语义 (中等) | 在线语义 (最优) |
| 离线可用 | 是 | 是 | 否 |
| 中文支持 | jieba 分词 | text2vec 模型 | 原生多语言 |
| 每次搜索成本 | 0 | 0 | ~¥0.001 |
