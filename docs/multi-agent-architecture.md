# Multi-Agent Architecture

本文档描述 OpenAkita 的多 Agent 架构设计。多 Agent 模式已默认常开，标记为 **Beta**。

## 设计原则

1. **默认常开** — 多 Agent 功能始终加载，不再需要手动开启
2. **渐进增强** — 数据结构改动向后兼容（新字段有默认值）
3. **轻量通信** — 废弃旧 ZMQ 方案，使用 `asyncio.Queue` + JSON 进行进程内通信
4. **安全沙箱** — AI 动态创建的 Agent 受权限继承、深度限制、生命周期约束

## 系统架构总览

```
┌───────────────────────────────────────────────────────────────────┐
│                         用户接口层                                  │
│   Desktop App (Tauri)  │  IM Channels (多 Bot)  │  CLI / API      │
└────────────┬───────────┴──────────┬──────────────┴────────┬───────┘
             │                      │                       │
             ▼                      ▼                       ▼
┌───────────────────────────────────────────────────────────────────┐
│                     MessageGateway                                │
│  • 统一消息路由          • @检测 / 群聊策略                         │
│  • IM 命令拦截            • 优雅关闭 (drain 模式)                   │
│  • /模式 /切换 /help      • 多 Bot 实例管理                        │
└────────────────────────────┬──────────────────────────────────────┘
                             │
              │
              ▼
           ┌──────────────────────┐
           │  AgentOrchestrator    │
           │  (现有流程)   │  │  ┌──────────────────┐ │
           │              │  │  │ ProfileStore     │ │
           │              │  │  │ AgentFactory     │ │
           │              │  │  │ InstancePool     │ │
           │              │  │  │ FallbackResolver │ │
           │              │  │  │ TaskQueue        │ │
           │              │  │  │ LockManager      │ │
           │              │  │  └──────────────────┘ │
           └──────────────┘  └──────────────────────┘
                     │              │
                     ▼              ▼
           ┌──────────────────────────────────────────┐
           │            ReasoningEngine                │
           │  • LLM 调用 + 工具执行 + 流式输出          │
           │  • Token 追踪 (per-agent 维度)            │
           └──────────────────────────────────────────┘
```

## 核心组件

### 1. AgentProfile — Agent 蓝图

```
src/openakita/agents/profile.py
```

定义 Agent 的身份和能力：

| 字段 | 说明 |
|------|------|
| `id` | 唯一标识，如 `code-assistant` |
| `name` / `name_i18n` | 显示名称 (支持多语言) |
| `description` / `description_i18n` | 功能描述 |
| `type` | `SYSTEM` / `CUSTOM` / `DYNAMIC` |
| `skills` | 携带的技能列表 |
| `skills_mode` | `ALL` / `INCLUSIVE` / `EXCLUSIVE` |
| `custom_prompt` | 自定义系统提示词后缀 |
| `icon` / `color` | 前端显示属性 |
| `fallback_profile_id` | 失败时的降级目标 |
| `created_by` | `system` / `user` / `ai` |

**ProfileStore** 负责持久化（JSON 文件），线程安全（`threading.RLock`），系统预置不可删改。

### 2. AgentOrchestrator — 中央协调器

```
src/openakita/agents/orchestrator.py
```

替代旧的 ZMQ `MasterAgent`，职责：

- **消息路由** — 根据 `session.context.agent_profile_id` 选择 Agent
- **委派管理** — 支持 Agent 间委派，深度限制 (`MAX_DELEGATION_DEPTH=5`)
- **超时/失败/取消** — `asyncio.wait_for` 超时，自动 fallback
- **健康监控** — 按 profile_id 追踪成功率、延迟、错误
- **SSE 通知** — 委派时通过 `handoff_events` 通知前端

**请求流程：**
```
handle_message(session, message)
    ├─ 获取 agent_profile_id
    ├─ _dispatch(session, message, profile_id, depth=0)
    │   ├─ 健康计数 +1
    │   ├─ asyncio.wait_for(_execute_agent(...), timeout)
    │   │   ├─ pool.get_or_create(session_id, profile)
    │   │   └─ agent.chat_with_session(...)
    │   ├─ 成功 → 记录延迟，fallback.record_success
    │   └─ 失败/超时 → fallback 检查 → 递归 _dispatch(depth+1)
    └─ 返回结果
```

### 3. AgentFactory + AgentInstancePool

```
src/openakita/agents/factory.py
```

- **AgentFactory** — 从 `AgentProfile` 创建 `Agent` 实例，应用技能过滤和自定义提示词
- **AgentInstancePool** — per-session 实例管理，空闲 30 分钟自动回收，background reaper 线程

### 4. FallbackResolver — 降级策略

```
src/openakita/agents/fallback.py
```

追踪每个 profile 的健康状态，连续失败超过阈值（默认 3 次）时建议/触发降级到 `fallback_profile_id`。

### 5. TaskQueue — 优先级任务队列

```
src/openakita/agents/task_queue.py
```

5 级优先级（URGENT → BACKGROUND），并发限制，支持取消。用于异步任务调度。

### 6. LockManager — 细粒度资源锁

```
src/openakita/agents/lock_manager.py
```

per-resource 异步锁，防止多 Agent 并发访问共享资源（文件、内存、工具）。支持过期清理。

## 系统预置 Agent

| ID | 图标 | 名称 | 技能 |
|----|------|------|------|
| `default` | 🤖 | 通用助手 | ALL |
| `office-doc` | 📄 | 办公文档 | docx, pptx, xlsx, pdf |
| `code-assistant` | 💻 | 代码助手 | shell, file, web_search |
| `browser-agent` | 🌐 | 浏览器代理 | browser |
| `data-analyst` | 📊 | 数据分析 | xlsx, shell, web_search |

首次开启多 Agent 模式时自动部署到 `data/agents/profiles/`。

## IM 多 Bot 架构

支持同一 IM 通道类型（如飞书）创建多个 Bot 实例，每个绑定不同 Agent：

```
im_bots:
  - id: "feishu-assistant"
    type: "feishu"
    agent_profile_id: "default"
    credentials: { app_id: "...", app_secret: "..." }
  - id: "feishu-coder"
    type: "feishu"
    agent_profile_id: "code-assistant"
    credentials: { app_id: "...", app_secret: "..." }
```

每个 Bot 实例的 `channel_name` 为 `{type}:{id}`（如 `feishu:feishu-coder`），Session 自然隔离。

### IM 命令体系

| 命令 | 说明 | 模式限制 |
|------|------|----------|
| `/模式` `/mode` | 查看/切换单多 Agent 模式 | 始终可用 |
| `/切换` `/switch` | 切换当前 Agent | 仅多 Agent |
| `/help` `/帮助` | 命令帮助 | 仅多 Agent |
| `/状态` `/status` | 当前 Agent 信息 | 仅多 Agent |
| `/重置` `/agent_reset` | 重置为默认 Agent | 仅多 Agent |

### 群聊响应策略

`GroupResponseMode` 支持三种模式：
- `always` — 所有消息都响应
- `mention_only` — 仅被 `@` 时响应
- `smart` — AI 判断是否响应，带 `SmartModeThrottle` 限流

## 记忆分层

```
MemoryScope:
  GLOBAL   — 所有 Agent 共享（默认，兼容旧数据）
  AGENT    — Agent 私有记忆
  SESSION  — 会话级记忆
```

SQLite `memories` 表增加 `scope` + `scope_owner` 列，旧数据默认 `GLOBAL`。

## AI 工具（仅多 Agent 模式注入）

### delegate_to_agent

AI 可委派任务给其他 Agent：
```json
{
  "agent_id": "code-assistant",
  "message": "请帮我写一个排序算法",
  "reason": "需要专业代码能力"
}
```

### create_agent

AI 可动态创建临时 Agent：
```json
{
  "name": "数据清洗专家",
  "description": "专门处理 CSV 数据清洗",
  "skills": ["shell", "file"],
  "custom_prompt": "你是一个数据清洗专家..."
}
```

**安全策略：**
- 每会话最多创建 3 个动态 Agent
- 委派深度最大 5 层
- 动态 Agent 不能再创建 Agent
- 生命周期最长 60 分钟

## Token 成本追踪

Token 使用记录增加 `agent_profile_id` 字段，支持 per-Agent 维度聚合：

```
GET /api/stats/tokens/by-agent?period=24h
→ { "by_agent": { "default": {...}, "code-assistant": {...} } }
```

## 前端界面

### 桌面端组件

| 组件 | 位置 | 功能 |
|------|------|------|
| 侧边栏 Beta 开关 | App.tsx | 切换多 Agent 模式 |
| Agent 选择器 | ChatView.tsx 输入框顶部 | 选择当前会话的 Agent |
| Agent 仪表盘 | AgentDashboardView.tsx | 状态卡片 + Bot/Agent 关系图 + 粒子动画 |
| Agent 管理器 | AgentManagerView.tsx | CRUD Agent + 自定义编辑器 |
| 委派气泡 | ChatView.tsx | SSE `agent_handoff` 事件展示 |

多 Agent UI 组件始终渲染（多 Agent 模式已默认常开）。

## 数据存储路径

```
{project_root}/data/
├── agents/                    # AgentProfile 持久化
│   └── profiles/
│       ├── default.json
│       ├── code-assistant.json
│       └── ...
├── sessions/                  # 会话数据 (含 agent_profile_id)
├── memory/                    # 记忆存储 (含 scope/scope_owner)
├── runtime_state.json         # 运行时状态 (含 im_bots)
└── agent.db                   # SQLite (含 token_usage.agent_profile_id)
```

## 与多工作区的兼容性

多 Agent 数据存储在 `settings.data_dir`（即 `project_root/data/`）下。当用户切换工作区时：

- `project_root` 变更 → `data_dir` 随之变更 → Agent profiles、sessions、memory 自然隔离
- 各工作区有独立的 Agent 配置和会话状态
- IM Bot 凭证（`im_bots`）存储在 `runtime_state.json`，跟随工作区

**注意事项：**
- `OPENAKITA_ROOT` 环境变量可覆盖 `openakita_home`，不影响 `data_dir`
- `skills_path` 在 `user_workspace_path/skills` 下，技能全局共享
- Agent profiles 是 per-workspace 的，但系统预置会在每个工作区首次启用时自动部署

## 旧架构废弃说明

`openakita.orchestration` 模块（基于 ZMQ 的 Master-Worker 架构）已标记为 `@deprecated`：

- 所有公共类（`AgentBus`, `MasterAgent`, `WorkerAgent`）在 `__init__` 中发出 `DeprecationWarning`
- `pyzmq` 已从核心依赖移至可选依赖：`pip install openakita[orchestration]`
- 代码未删除，但不再维护
- 新功能请使用 `openakita.agents` 包

## API 端点汇总

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config/agent-mode` | 获取多 Agent 模式状态 |
| POST | `/api/config/agent-mode` | 切换多 Agent 模式 |
| GET | `/api/agents/profiles` | 列出 Agent profiles |
| POST | `/api/agents/profiles` | 创建自定义 Agent |
| PUT | `/api/agents/profiles/{id}` | 更新 Agent |
| DELETE | `/api/agents/profiles/{id}` | 删除 Agent |
| GET | `/api/agents/bots` | 列出 IM Bot 配置 |
| POST | `/api/agents/bots` | 创建 Bot |
| PUT | `/api/agents/bots/{id}` | 更新 Bot |
| DELETE | `/api/agents/bots/{id}` | 删除 Bot |
| POST | `/api/agents/bots/{id}/toggle` | 启禁 Bot |
| GET | `/api/agents/health` | Agent 健康指标 |
| GET | `/api/agents/collaboration/{session_id}` | 协作信息 |
| GET | `/api/stats/tokens/by-agent` | per-Agent Token 统计 |
