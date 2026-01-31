# 记忆系统架构

> 最后更新: 2026-01-31

## 一、整体架构

```mermaid
flowchart TB
    subgraph storage [存储层]
        CHROMA[(ChromaDB 向量库)]
        JSON[memories.json]
        MEMMD[MEMORY.md 精华]
    end
    
    subgraph global [全局记忆]
        PREF[用户偏好 PREFERENCE]
        RULE[规则约束 RULE]
        SKILL[成功模式 SKILL]
        ERROR[错误教训 ERROR]
        FACT[重要事实 FACT]
    end
    
    subgraph session [Session 级别]
        MSG[对话历史 messages]
        TASK[当前任务 current_task]
        VAR[会话变量 variables]
    end
    
    subgraph daily [每日归纳]
        HISTORY[conversation_history]
        SUMMARY[daily_summaries]
    end
    
    global --> JSON
    global --> CHROMA
    JSON -->|每日刷新精华| MEMMD
    MSG -->|AI判断提取| global
    HISTORY -->|凌晨归纳| SUMMARY
    SUMMARY -->|精华| global
```

## 二、三层存储设计

| 存储 | 用途 | 更新频率 | 注入方式 |
|------|------|----------|----------|
| **ChromaDB** | 向量索引，语义搜索 | 实时（有新记忆就索引） | 按需搜索相关记忆 |
| **memories.json** | 完整记忆库 | 实时 | 作为 ChromaDB 的元数据 |
| **MEMORY.md** | 精华摘要（最重要的 10-15 条） | 每日凌晨刷新 | 每次系统提示都带上 |

## 三、数据流

```mermaid
sequenceDiagram
    participant User
    participant Session
    participant Agent
    participant AI as AI判断器
    participant Vector as VectorStore
    participant Memory as MemoryManager
    participant Scheduler
    
    User->>Session: 发送消息
    Session->>Agent: chat_with_session()
    
    Note over Agent: 构建系统提示
    Agent->>Memory: 读取 MEMORY.md 精华
    Agent->>Vector: 语义搜索相关记忆
    Vector-->>Agent: 相关记忆列表
    
    Agent->>Agent: 处理并响应
    Agent->>AI: 判断是否提取记忆
    
    alt 有值得记录的信息
        AI-->>Memory: 新记忆
        Memory->>Vector: 计算 embedding 并存入
    end
    
    Note over Scheduler: 每日凌晨 3:00
    Scheduler->>Memory: consolidate_daily()
    Memory->>Memory: 读取所有对话历史
    Memory->>AI: 归纳精华
    AI-->>Memory: 长期记忆
    Memory->>Memory: 刷新 MEMORY.md
```

## 四、记忆类型

```mermaid
classDiagram
    class Memory {
        +str id
        +MemoryType type
        +MemoryPriority priority
        +str content
        +str source
        +list~str~ tags
        +datetime created_at
        +datetime updated_at
        +int access_count
        +float importance_score
    }
    
    class MemoryType {
        <<enumeration>>
        FACT
        PREFERENCE
        SKILL
        ERROR
        RULE
        CONTEXT
    }
    
    class MemoryPriority {
        <<enumeration>>
        TRANSIENT
        SHORT_TERM
        LONG_TERM
        PERMANENT
    }
    
    Memory --> MemoryType
    Memory --> MemoryPriority
```

| 类型 | 说明 | 示例 |
|------|------|------|
| FACT | 事实信息 | "用户的代码目录在 D:\code" |
| PREFERENCE | 用户偏好 | "用户喜欢用 Python" |
| SKILL | 成功模式 | "用 pytest 测试更可靠" |
| ERROR | 错误教训 | "直接删除文件会导致数据丢失" |
| RULE | 规则约束 | "不要有太强的风险意识" |
| CONTEXT | 上下文 | "当前项目是 myagent" |

| 优先级 | 保留时长 | 说明 |
|--------|----------|------|
| TRANSIENT | 会话结束后删除 | 临时信息 |
| SHORT_TERM | 3 天 | 短期记忆 |
| LONG_TERM | 数周 | 长期记忆 |
| PERMANENT | 永不删除 | 重要规则、核心偏好 |

## 五、组件关系

```mermaid
flowchart LR
    subgraph core [核心组件]
        Agent[Agent]
        Brain[Brain/LLM]
        Identity[Identity]
    end
    
    subgraph memory [记忆组件]
        MM[MemoryManager]
        VS[VectorStore]
        EX[MemoryExtractor]
        DC[DailyConsolidator]
    end
    
    subgraph session [会话组件]
        SM[SessionManager]
        S[Session]
    end
    
    subgraph scheduler [调度组件]
        TS[TaskScheduler]
        TE[TaskExecutor]
    end
    
    Agent --> MM
    Agent --> Identity
    Agent --> Brain
    MM --> VS
    MM --> EX
    MM --> DC
    Agent --> SM
    SM --> S
    Agent --> TS
    TS --> TE
    DC -.->|凌晨触发| TS
```

## 六、上下文获取流程

### IM 通道 (Telegram/Feishu)

```mermaid
sequenceDiagram
    participant Adapter as ChannelAdapter
    participant Gateway as MessageGateway
    participant SM as SessionManager
    participant Session
    participant Agent
    participant MM as MemoryManager
    
    Adapter->>Gateway: 收到消息
    Gateway->>SM: get_session()
    SM-->>Gateway: Session
    Gateway->>Session: add_message(user)
    Session-->>Gateway: session_messages
    Gateway->>Agent: chat_with_session(session_messages)
    Agent->>MM: record_turn(user)
    Agent->>Agent: 处理
    Agent->>MM: record_turn(assistant)
    Agent-->>Gateway: response
    Gateway->>Session: add_message(assistant)
    Gateway->>Adapter: 发送响应
```

### 定时任务

```mermaid
sequenceDiagram
    participant TS as TaskScheduler
    participant TE as TaskExecutor
    participant Session as TaskSession
    participant Agent
    participant MM as MemoryManager
    
    TS->>TE: execute(task)
    TE->>Session: 获取/创建任务 Session
    Session-->>TE: session_messages
    TE->>Agent: chat_with_session(session_messages)
    Agent->>MM: record_turn()
    Agent-->>TE: result
    TE->>Session: 保存执行记录
```

## 七、每日归纳流程

```mermaid
flowchart TB
    START[凌晨 3:00 触发] --> READ[读取 conversation_history]
    READ --> EXTRACT[LLM 提取精华]
    EXTRACT --> DEDUP[去重合并]
    DEDUP --> SAVE[存入 memories.json]
    SAVE --> INDEX[向量化存入 ChromaDB]
    INDEX --> REFRESH[刷新 MEMORY.md]
    REFRESH --> CLEANUP[清理过期历史]
    CLEANUP --> END[完成]
```

## 八、数据目录结构

```
data/memory/
├── memories.json              # 全局记忆（JSON，完整数据）
├── chromadb/                  # ChromaDB 向量索引
│   └── chroma.sqlite3
├── daily_summaries/           # 每日归纳摘要
│   ├── 2026-01-30.json
│   └── 2026-01-31.json
└── conversation_history/      # 原始对话历史（按日期）
    ├── 2026-01-30/
    │   ├── session_001.jsonl
    │   └── session_002.jsonl
    └── 2026-01-31/
        └── session_003.jsonl

data/scheduler/
└── task_sessions/             # 定时任务专属 Session
    └── {task_id}.json

identity/MEMORY.md             # 精华摘要
```

## 九、容量限制

| 限制 | 默认值 | 说明 |
|------|--------|------|
| MAX_HISTORY_DAYS | 30 | 超过 30 天的历史文件删除 |
| MAX_HISTORY_FILES | 1000 | 超过 1000 个文件删除最旧的 |
| MAX_HISTORY_SIZE_MB | 500 | 超过 500MB 删除最旧的 |
| MEMORY_MD_MAX_CHARS | 800 | MEMORY.md 精华最多 800 字符 |

## 十、性能指标

| 操作 | 耗时 | 备注 |
|------|------|------|
| 首次加载 embedding 模型 | ~5-10s | 仅启动时一次 |
| 单条记忆向量化 | ~50ms | 实时可接受 |
| 向量搜索 (100条记忆) | <10ms | ChromaDB 很快 |
| MEMORY.md 读取 | <1ms | 每次请求都读 |
| 每日归纳 | ~1-2min | 凌晨执行 |
| 首次下载模型 | ~1-5min | 约 100MB |

## 十一、关键文件

| 文件 | 说明 |
|------|------|
| `src/openakita/memory/manager.py` | 记忆管理器，核心协调组件 |
| `src/openakita/memory/vector_store.py` | 向量存储，语义搜索 |
| `src/openakita/memory/extractor.py` | AI 记忆提取器 |
| `src/openakita/memory/daily_consolidator.py` | 每日归纳器 |
| `src/openakita/memory/types.py` | 记忆类型定义 |
| `src/openakita/core/identity.py` | 身份管理，加载 MEMORY.md |
| `src/openakita/sessions/session.py` | Session 定义 |
| `src/openakita/scheduler/executor.py` | 定时任务执行器 |
