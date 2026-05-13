# Security Architecture v2 — 重构调研档案 (Commit 0)

> 本文是 OpenAkita Security Architecture v2 重构（plan: `security_architecture_v2_31fbf920.plan.md`）的 **Commit 0 调研落档**。
>
> 写作目标：把所有"决策依据 + 现有代码事实 + 隐患/Bug 清单 + 工具→ApprovalClass 映射 + 依赖图"集中在一份可检索文档里，让后续 11 个 commit 的执行者（也包括我自己回看）能在一处获得权威数据，不用再次扫全仓。
>
> 写作范围：仅文字 + 表格，零代码改动。
>
> 维护规则：每个后续 commit (C1-C18) 完成时，回到本文档对应章节追加"实施记录"段落，记录"实际修改的文件 + 偏离 plan 的地方 + 新发现的事实"。

---

## 0. 阅读路径

| 你想知道什么 | 看哪一节 |
|---|---|
| v2 重构总体动机与现有 v1 的痛点 | §1 |
| 5 处现存严重 Bug 详情（含日志/复现路径）| §2 |
| 4 轮复盘共发现的 75 项隐患/遗漏 | §3 应对总表 |
| 150+ 内置工具的 ApprovalClass 初始映射 | §4 |
| 删旧 policy.py 时哪些符号必须 re-export | §5 |
| 外部依赖图（谁 import 了 policy/permission）| §6 |
| 现有 POLICIES.yaml 的完整 schema | §7 |
| 现有 SSE confirm 协议的真实字段（不是猜的）| §8 |
| 现有 IM 适配器列表 + owner 判断现状 | §9 |
| 现有 handler 注册位置（重大设计简化）| §10 |
| 现有持久化文件清单 | §11 |
| 11/18 commit 的对应表 | §12 |
| 开发者新增工具的完整 SOP（4 方案 + 决策树）| §4.21 |
| Commit 19 设计：4 层护栏（CI / 启动 WARN / docstring / Cursor rule）| §12.5 |

---

## 1. 现状与痛点（v2 重构的发起原因）

OpenAkita 当前的安全/权限决策代码分散在 4 处，互相**并行运行**而不是串联：

1. **`src/openakita/core/policy.py`**（1783 行，含 7 种 confirmation 模式逻辑）— `PolicyEngine`：zone 矩阵 + shell pattern + checkpoint + sandbox + ui_confirm + audit + death_switch + user_allowlist
2. **`src/openakita/core/permission.py`**（OpenCode 风格）— `Ruleset` + `PLAN_MODE_RULESET` + `ASK_MODE_RULESET` + `COORDINATOR_MODE_RULESET` + `disabled()` + `EDIT_TOOLS` + `READ_TOOLS`
3. **`src/openakita/core/agent.py:RiskGate`** — `_check_trusted_path_skip` + `_check_trust_mode_skip` + `_consume_risk_authorization` + `classify_risk_intent`（pre-LLM 层）
4. **`src/openakita/core/reasoning_engine.py`** + **`tool_executor.py`** — 双重检查 `policy_engine.assert_tool_allowed` + `check_permission`

**用户原始投诉**（2026-05-12 12:14:08 日志摘录）：

```
[Policy] confirm: write_file — 信任模式下仍需确认高风险操作: 覆盖写入已有文件
[Permission] CONFIRM write_file in agent mode: policy=TrustModeDangerousOperation
```

> 用户开了 trust 模式，仍被要求确认覆盖桌面 .txt 文件。根因是 `RiskGate` (L1) 不尊重 trust 模式，而 `PolicyEngine` (L2) 早已放行——**两层逻辑互不知情**。

**v2 目标**：

- **唯一决策入口**：`PolicyEngineV2.evaluate_tool_call()` + `evaluate_message_intent()` 两个函数，全仓只在两处被调用（`tool_executor.execute_tool_with_policy` + `agent.RiskGate`）
- **正交两层 mode**：`session_role` (plan/ask/agent/coordinator) × `confirmation_mode` (default/accept_edits/trust/strict/dont_ask)
- **11 维 ApprovalClass**：以工具语义+参数为核心的分类，替代旧 zone-only 决策
- **修复 5 处现存 Bug**：见 §2
- **填补无人值守审批黑洞**：4 种 unattended strategy（含 IM 卡片审批）
- **全场景覆盖**：multi-agent / org / IM / CLI / API / Webhook / scheduled / evolution / system_task / Skill / MCP / plugin

---

## 2. 现存 Bug 清单（5 处，必修）

### 2.1 `tool_executor.execute_batch` confirm 撒谎 Bug（最严重）

**文件**：[`src/openakita/core/tool_executor.py:804-846`](../src/openakita/core/tool_executor.py)

**现象**：scheduled task / org delegate / spawn_agent / sub-agent 等所有走 `Agent.execute_task` 路径的工具调用，遇到 `PolicyDecision.CONFIRM` 时返回伪造的 tool_result：

```python
return (idx, {
    "type": "tool_result",
    "content": "⚠️ 需要用户确认: ...\n已向用户发送确认请求，请等待用户通过界面做出决定后再继续。",
    "is_error": True,
    "_security_confirm": {...},  # ← 没有任何下游代码消费这个字段
})
```

**实际行为**：
- **没有**调 `store_ui_pending`
- **没有**yield `security_confirm` SSE 事件
- **没有**push 到 IM
- **没有**`wait_for_ui_resolution`
- LLM 收到"已通知用户"假消息后会 **乱来**：继续尝试 / 用 `ask_user` / 死循环

**影响范围**：所有非交互式 LLM 调用路径（cron / org / spawn / sub-agent）。这是一个**架构空缺**，源码无 TODO 注释，几乎没人意识到。

**v2 修复**：
- plan §14 引入 `is_unattended` + 4 种 strategy + `pending_approvals` 持久化 + `DeferredApprovalRequired` 异常（C12 实施）
- plan §15 sub-agent confirm 全冒泡到 root_user（C13 实施）
- 删除"撒谎"代码，让 confirm 真正走完整链路

### 2.2 `switch_mode` 工具实际不生效

**文件**：[`src/openakita/tools/handlers/mode.py:18-46`](../src/openakita/tools/handlers/mode.py)

```python
session = getattr(self.agent, "session", None)
if session and hasattr(session, "mode"):
    current_mode = session.mode
    ...
    session.mode = target_mode
```

**问题**：[`src/openakita/sessions/session.py`](../src/openakita/sessions/session.py) 的 `Session` dataclass **没有** `mode` 字段。`hasattr(session, "mode")` 永远是 False，工具静默失败。

**v2 修复**：Commit 8 给 `Session` 加 `session_role: SessionRole` + `confirmation_mode: ConfirmationMode` 两个字段，`switch_mode` 工具改成更新前者。`__post_init__` 用 `getattr` + default 兼容旧 sessions.json。

### 2.3 IM 前缀 conversation 直接报错不 yield SSE

**文件**：[`src/openakita/core/reasoning_engine.py:4390-4398, 4780-4813`](../src/openakita/core/reasoning_engine.py)

```python
_IM_CONVERSATION_PREFIXES = ("qqbot:", "feishu:", "dingtalk:", "wework_ws:", "telegram:", "onebot:")

def _is_im_conversation(conversation_id: str | None) -> bool:
    return str(conversation_id).startswith(_IM_CONVERSATION_PREFIXES) if conversation_id else False
```

**现象**：IM 前缀的会话遇到 confirm 时，reasoning_engine 直接报错"需要桌面确认"结束，**永远不 yield `security_confirm` 事件**，导致 [`gateway._handle_im_security_confirm`](../src/openakita/channels/gateway.py) 永远收不到事件 → IM 卡片确认链路实际不工作。

**v2 修复**：§8.3 删掉早退逻辑：
- IM 渠道 + ApprovalClass ≠ {`interactive`, `desktop`, `browser`} → 正常 yield SSE → gateway 接住 → IM 卡片
- 仅 ApprovalClass = `interactive`（如 `desktop_click`）时才 deny（这些工具在 IM 上无意义）

### 2.4 `consume_session_trust` 不真删过期规则

**文件**：[`src/openakita/core/trusted_paths.py`](../src/openakita/core/trusted_paths.py)

**现象**：`consume_session_trust()` 发现过期 trust override 时，仅"跳过不消费"而**不从 `session.metadata["trusted_path_overrides"]` 真正删除**。长会话累积下来 metadata 不断膨胀。

**v2 修复**：Commit 8 改 `consume_session_trust` 在过期判定时同时 `del overrides[key]`。

### 2.5 `POST /api/config/security` 整段覆盖

**文件**：[`src/openakita/api/routes/config.py:write_security_config`](../src/openakita/api/routes/config.py)

```python
data["security"] = body.security  # ← 整段替换，丢失用户的 user_allowlist / custom_critical 等
```

**现象**：用户通过 SecurityView 改一个开关 → 后端用前端传来的 body 整段覆盖 yaml `security` 节 → 用户之前手工加的 100 条 `user_allowlist.commands` 直接消失。

**v2 修复**：§7.2 改 deep-merge：

```python
def _deep_merge(target: dict, source: dict) -> dict:
    for k, v in source.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_merge(target[k], v)
        else:
            target[k] = v
    return target

_deep_merge(data.setdefault("security", {}), body.security)
```

---

## 3. 4 轮复盘的 75 项隐患/遗漏应对总表

### 3.1 R1 第一轮（v1 → v2 修订）— 12 项

| # | 隐患 | 应对章节 | Commit |
|---|---|---|---|
| 1 | `evaluate(ToolCallEvent \| MessageIntentEvent)` 联合类型混乱 | plan §1 拆两入口 | C3 |
| 2 | `tool_metadata.py` 静态查表 hardcode ApprovalClass | plan §2 ApprovalClassifier 分类器链 | C2 |
| 3 | `PermissionMode` 与 `plan/ask/agent` 混用 | plan §3 正交两层 | C1/C3 |
| 4 | `safety_immune.paths` 默认包 `data/**` 太宽 | plan §4 精细 9 类路径 | C6 |
| 5 | `POLICIES.yaml` 直接覆盖 | plan §7 in-place merge + deep-merge | C7 |
| 6 | reasoning_engine 双重检查 | C4 删 `assert_tool_allowed` 两处 | C4 |
| 7 | IM 前缀 confirm bug（同 §2.3） | plan §8.3 | C6 |
| 8 | "加 4 选项 SSE"实际已是 5 个 | plan §8.1 沿用现有 + 标准化命名 | C9 |
| 9 | `switch_mode` / `consume_session_trust` 现存 bug（同 §2.2/2.4）| Commit 8 顺手修 | C8 |
| 10 | checkpoint/sandbox/death_switch 集成不明 | plan §6 ApprovalClass 触发 | C3/C8 |
| 11 | 删 policy.py 1964 行迁移不清 | plan §6.2 拆 5 段迁移 + Commit 8 薄壳 | C8 |
| 12 | 子 agent permission 上下文丢失 | plan §15 root_session 透传 | C13 |

### 3.2 R2 第二轮（架构纵深）— 14 项

| # | 隐患 | 应对章节 | Commit |
|---|---|---|---|
| R2-1 | 30s replay authorization 机制丢失 | plan §3.5 replay_authorization | C5 |
| R2-2 | LLM 工具列表层 `_filter_tools_by_mode` 怎么办 | plan §3.7 走 v2 矩阵 | C4 |
| R2-3 | `_frontend_mode` + 旧 API `/permission-mode` 过渡 | Commit 8 双写兼容 | C8 |
| R2-4 | `command_patterns` 黑名单在哪一步检查 | plan §3.2 step 1c | C3 |
| R2-5 | `needs_sandbox` + `shell_risk_level` 字段 | plan §6.1 ApprovalClassifier 一次性算 | C2/C3 |
| R2-6 | sandbox 选项作为特殊 allow 的语义 | plan §6.2 末尾 | C4 |
| R2-7 | `_apply_permission_mode_defaults` 副作用清理 | Commit 8 一次性清理 | C8 |
| R2-8 | plan/ask/agent/coordinator × ApprovalClass 矩阵 | plan §3.4 二维矩阵 | C3 |
| R2-9 | `zones.default_zone` 兜底语义 | plan §3.2 末尾 + §7.1 旧 zone 合并 | C3/C7 |
| R2-10 | `trusted_paths.consume_session_trust` step 2b 对接 | plan §3.2 step 2b | C5 |
| R2-11 | 新增 `tool_intent_preview` SSE 事件 | plan §8.4 | C4/C9 |
| R2-12 | 插件 `mutates_params` 强制审计 | Commit 10 jsonl 审计 | C10 |
| R2-13 | `coordinator` 模式 5×11 二维矩阵 | plan §3.4 + §3.6 | C3 |
| R2-14 | 现有 13 个测试文件迁移清单 | plan §9.5 | C4-C10 |

### 3.3 R3 第三轮（计划任务系统）— 5 项

| # | 隐患 | 应对章节 | Commit |
|---|---|---|---|
| R3-1 | `tool_executor.execute_batch` confirm 撒谎 bug（同 §2.1）| plan §14.1 | C12 |
| R3-2 | `is_unattended` / `unattended_strategy` 字段缺失 | plan §14.2 | C12 |
| R3-3 | PolicyEngineV2 step 1.5 unattended 决策分支 | plan §14.3 | C12 |
| R3-4 | pending_approvals 持久化 + IM 卡片 + PendingApprovalsView | plan §14.5/14.8/14.10 | C12 |
| R3-5 | "批准重跑 + 30s replay" resume 策略 | plan §14.7 | C12 |

### 3.4 R4 第四轮（被忽视场景）— 22 项

| # | 隐患 | 应对章节 | Commit |
|---|---|---|---|
| R4-1 | sub-agent confirm 推到错误 channel/黑洞 | plan §15.4 全冒泡到 root | C13 |
| R4-2 | `delegate_parallel` N 个 sub 同 confirm 重复弹 | plan §15.5 confirm_dedup | C13 |
| R4-3 | `spawn_agent` 异步派生后无 owner | plan §15.6 视 unattended + owner=root | C13 |
| R4-4 | org root → specialist 多层 delegate confirm | plan §15.7 delegate_chain 透传 | C13 |
| R4-5 | CLI 模式 confirm UX 不明 | plan §16.2 prompt_toolkit | C14 |
| R4-6 | HTTP API 客户端无 SSE 能力 | plan §16.3 202 + poll url | C14 |
| R4-7 | Webhook 入口 headless 处理 | plan §16.4 永 unattended | C14 |
| R4-8 | 管道输入 stdin 关闭 | plan §16.1 isatty 检测 | C14 |
| R4-9 | Evolution 与 safety_immune 冲突 | plan §17.1 时窗例外 | C15 |
| R4-10 | system 任务旁路 PolicyEngine | plan §17.2 SYSTEM_TASKS.yaml | C15 |
| R4-11 | Workspace backup 一致性 | plan §17.2 同上 | C15 |
| R4-12 | Skill 自报 risk_class 撒谎 | plan §17.3 信任度严格度取大 | C15 |
| R4-13 | MCP server 自报撒谎 | plan §17.3 同上 | C15 |
| R4-14 | Prompt injection from tool result | plan §18.1-18.3 marker + system 加固 | C16 |
| R4-15 | POLICIES.yaml 恶意修改 | plan §18.4 pydantic v2 严格校验 | C16 |
| R4-16 | execute_task 进程崩溃中断 | plan §19.1 lock 文件 | C17 |
| R4-17 | Scheduler 重启丢失 awaiting | plan §19.2 启动扫描 | C17 |
| R4-18 | 同用户桌面+IM 同时活跃 | plan §19.3 subscribers + 第一 resolve | C17 |
| R4-19 | SSE 断连 confirm 续传 | plan §19.4 Last-Event-ID | C17 |
| R4-20 | 同会话连续 confirm 烦躁 | plan §20.1 5s 窗口聚合 | C18 |
| R4-21 | POLICIES.yaml hot-reload | plan §20.2 watchdog + atomic swap | C18 |
| R4-22 | ENV 覆盖配置 | plan §20.3 5 个 ENV 变量 | C18 |

### 3.5 R5 第五轮（subagent 全仓 grep + 100 项自检）— 22 项

| # | 隐患 | 应对章节 | Commit |
|---|---|---|---|
| R5-1 | `config.py` import `policy.py` 私有常量 | plan §8 + §21.1 薄壳 re-export | C8 |
| R5-2 | `tests/e2e/test_p0_regression.py` 直接 import `_ZONE_OP_MATRIX` | plan §21.1 薄壳保留私有名 | C8 |
| R5-3 | `tests/integration/test_gateway.py` Fake `PolicyEngine` | plan §21.1 提供 `policy_v2.testing.FakeEngine` | C4 |
| R5-4 | `audit_logger.py` / `checkpoint.py` init 时调 `get_policy_engine` | plan §22.2 12 步启动顺序 | C8 |
| R5-5 | `channels/policy.py` 误删 | plan §8 + §21.1 明示**不删** | C8 |
| R5-6 | handler 注册不在 30 个文件而在 `agent.py` 一处 | plan §2.4 + §10 重大简化 | C2 |
| R5-7 | `plugins/api.py:_check_permission` 与 PolicyEngine 分离 | plan §21.3 显式桥接 | C10 |
| R5-8 | `/api/health` 不检查 engine readiness | plan §22.4 readiness probe | C17 |
| R5-9 | `orgs/runtime.py` patches `execute_tool_with_policy` | plan §21.1 + C4 签名兼容 | C4 |
| R5-10 | `identity.py` runtime patch `tool_policies/auto_confirm` | plan §21.1 + §7 deep-merge | C7 |
| R5-11 | `docs/configuration.md` 提 `--auto-confirm` 但代码无 | plan §21.4 文档同步 | C18 |
| R5-12 | `orgs/event_store.py` 独立 audit 系统 | plan §21.1 + §22.4 保留独立 | C17 |
| R5-13 | 回滚策略缺失 | plan §22.1 atomic commit + revert 命令 | All |
| R5-14 | PolicyEngine thread-safety 未明示 | plan §22.3 完整保护表 | C3 |
| R5-15 | PolicyEngine 自身崩溃 fail-safe | plan §22.4 try/except + deny 兜底 | C4 |
| R5-16 | ContextVar 跨 spawn task 不传递 | plan §15.3 + §22.3 显式序列化 | C13 |
| R5-17 | audit jsonl 防篡改 | plan §22.5 加 safety_immune + hash chain | C6/C17 |
| R5-18 | 零配置首次安装无 e2e 验证 | plan §22.8 新增 test | C11 |
| R5-19 | 多平台测试矩阵未明示 | plan §22.7 Win/macOS/Linux | C11 |
| R5-20 | 配置 dry-run preview 缺失 | plan §22.6 SecurityView 加预览按钮 | C18 |
| R5-21 | Skill/MCP `trust_level` 字段 | plan §17.3 + §21.1 metadata 都加 | C15 |
| R5-22 | IM `group_policy.json` 与 owner_only 关系 | plan §21.1 AND 关系明示 | C6 |

---

## 4. 工具 → ApprovalClass 初始映射表（来自 30+ handler 的 `TOOLS` 属性）

> 这是 ApprovalClassifier 第 1 步"工具自身 metadata"的权威源数据。Commit 2 在 [`agent.py:_init_handlers`](../src/openakita/core/agent.py) 集中处把这些值通过 `tool_classes={...}` 注入。
>
> 标记说明：`*` = 启发式可改写（参数细化）；`!` = 跨平台/可选注册（Windows 专属或依赖）。

### 4.1 Filesystem（`filesystem.py:76-86`）

| 工具 | ApprovalClass 初始值 | 说明 |
|---|---|---|
| `run_shell` | `EXEC_CAPABLE`* | `_refine` 按 shell_risk_level 升降到 `DESTRUCTIVE` / `EXEC_LOW_RISK` |
| `write_file` | `MUTATING_SCOPED`* | `_refine` 按 path 是否在 workspace 升级 `MUTATING_GLOBAL` |
| `read_file` | `READONLY_GLOBAL` | |
| `edit_file` | `MUTATING_SCOPED`* | 同 write_file |
| `list_directory` | `READONLY_GLOBAL` | |
| `grep` | `READONLY_SEARCH` | |
| `glob` | `READONLY_SEARCH` | |
| `move_file` | `MUTATING_SCOPED`* | 同 write_file（src 与 dst 都要看）|
| `delete_file` | `DESTRUCTIVE` | 永远 ask |

### 4.2 Memory（`memory.py:36-47`）

| 工具 | ApprovalClass | |
|---|---|---|
| `consolidate_memories` | `CONTROL_PLANE` | 整理记忆，可能批量修改 |
| `add_memory` | `MUTATING_SCOPED` | 写 data/memory/* |
| `search_memory` | `READONLY_SEARCH` | |
| `get_memory_stats` | `READONLY_SCOPED` | |
| `list_recent_tasks` | `READONLY_SCOPED` | |
| `search_conversation_traces` | `READONLY_SEARCH` | |
| `trace_memory` | `READONLY_SEARCH` | |
| `search_relational_memory` | `READONLY_SEARCH` | |
| `get_session_context` | `READONLY_SCOPED` | |
| `memory_delete_by_query` | `DESTRUCTIVE` | + owner_only |

### 4.3 Browser（`browser.py:65-80`）

所有 `browser_*` + `view_image` 默认 `INTERACTIVE`（IM 渠道下应 deny）：
`browser_open`, `browser_navigate`, `browser_click`, `browser_type`, `browser_scroll`, `browser_wait`, `browser_execute_js`*, `browser_get_content`, `browser_screenshot`, `browser_list_tabs`, `browser_switch_tab`, `browser_new_tab`, `browser_close`, `view_image`

`browser_execute_js` 单独标 `EXEC_CAPABLE`（任意 JS 可读 cookie/exfil）。

### 4.4 Scheduled（`scheduled.py:26-33`）

| 工具 | ApprovalClass | 说明 |
|---|---|---|
| `schedule_task` | `CONTROL_PLANE` | + owner_only |
| `list_scheduled_tasks` | `READONLY_SCOPED` | |
| `cancel_scheduled_task` | `CONTROL_PLANE` | + owner_only |
| `update_scheduled_task` | `CONTROL_PLANE` | + owner_only |
| `trigger_scheduled_task` | `CONTROL_PLANE` | + owner_only |
| `query_task_executions` | `READONLY_SCOPED` | |

### 4.5 MCP（`mcp.py:35-44`）

| 工具 | ApprovalClass | 说明 |
|---|---|---|
| `call_mcp_tool` | `UNKNOWN`* | `_classify_mcp` 按 server:tool 名 + MCP `tool.annotations` 细化（trust_level 决定是否信任）|
| `list_mcp_servers` | `READONLY_SCOPED` | |
| `get_mcp_instructions` | `READONLY_SCOPED` | |
| `add_mcp_server` | `CONTROL_PLANE` | + owner_only |
| `remove_mcp_server` | `CONTROL_PLANE` | + owner_only |
| `connect_mcp_server` | `CONTROL_PLANE` | |
| `disconnect_mcp_server` | `CONTROL_PLANE` | |
| `reload_mcp_servers` | `CONTROL_PLANE` | + owner_only |

### 4.6 Profile（`profile.py:22-26`）

| 工具 | ApprovalClass | |
|---|---|---|
| `update_user_profile` | `MUTATING_SCOPED` | |
| `skip_profile_question` | `MUTATING_SCOPED` | |
| `get_user_profile` | `READONLY_SCOPED` | |

### 4.7 Plan / Todo（`todo_handler.py:37-44`）

| 工具 | ApprovalClass | |
|---|---|---|
| `create_todo` | `MUTATING_SCOPED` | |
| `update_todo_step` | `MUTATING_SCOPED` | |
| `get_todo_status` | `READONLY_SCOPED` | |
| `complete_todo` | `MUTATING_SCOPED` | |
| `create_plan_file` | `MUTATING_SCOPED` | 写 data/plans/* |
| `exit_plan_mode` | `CONTROL_PLANE` | |

### 4.8 System（`system.py:25-33`）

| 工具 | ApprovalClass | |
|---|---|---|
| `ask_user` | `INTERACTIVE` | |
| `enable_thinking` | `CONTROL_PLANE` | |
| `get_session_logs` | `READONLY_SCOPED` | |
| `get_tool_info` | `READONLY_SEARCH` | |
| `generate_image` | `NETWORK_OUT` | |
| `set_task_timeout` | `CONTROL_PLANE` | |
| `get_workspace_map` | `READONLY_SCOPED` | |

### 4.9 IM Channel（`im_channel.py:45-54`）

所有 IM channel 工具默认 `READONLY_SCOPED`（仅读 IM 数据），除 `deliver_artifacts` = `MUTATING_SCOPED`（推送内容到聊天）：
`deliver_artifacts`, `get_voice_file`, `get_image_file`, `get_chat_history`, `get_chat_info`, `get_user_info`, `get_chat_members`, `get_recent_messages`

### 4.10 Skills（`skills.py:42-53`）

| 工具 | ApprovalClass | |
|---|---|---|
| `list_skills` | `READONLY_SCOPED` | |
| `get_skill_info` | `READONLY_SCOPED` | |
| `run_skill_script` | `EXEC_CAPABLE`* | 视脚本内容细化 |
| `get_skill_reference` | `READONLY_SCOPED` | |
| `install_skill` | `CONTROL_PLANE` | + owner_only |
| `load_skill` | `CONTROL_PLANE` | |
| `reload_skill` | `CONTROL_PLANE` | |
| `manage_skill_enabled` | `CONTROL_PLANE` | |
| `execute_skill` | 由 SKILL.md `risk_class` 决定，缺省 `MUTATING_GLOBAL` | trust_level=default 时严格度取大 |
| `uninstall_skill` | `DESTRUCTIVE` | + owner_only |

### 4.11 Web Search / Web Fetch / Search（`web_search.py:145`, `web_fetch.py:167`, `search.py:17`）

| 工具 | ApprovalClass |
|---|---|
| `web_search` | `READONLY_SEARCH` |
| `news_search` | `READONLY_SEARCH` |
| `web_fetch` | `NETWORK_OUT` |
| `semantic_search` | `READONLY_SEARCH` |

### 4.12 Code Quality / LSP / Notebook（`code_quality.py:23`, `lsp.py:177`, `notebook.py:29`）

| 工具 | ApprovalClass |
|---|---|
| `read_lints` | `READONLY_SCOPED` |
| `lsp` | `READONLY_GLOBAL` |
| `edit_notebook` | `MUTATING_SCOPED`* |

### 4.13 Mode（`mode.py:18`）

| 工具 | ApprovalClass |
|---|---|
| `switch_mode` | `CONTROL_PLANE` |

### 4.14 Persona / Sticker / Plugins / Tool Search / Sleep / Worktree / Structured Output

| 工具 | ApprovalClass | 来源 |
|---|---|---|
| `switch_persona` | `CONTROL_PLANE` + owner_only | persona.py |
| `update_persona_trait` | `CONTROL_PLANE` | persona.py |
| `toggle_proactive` | `CONTROL_PLANE` | persona.py |
| `get_persona_profile` | `READONLY_SCOPED` | persona.py |
| `send_sticker` | `MUTATING_SCOPED` | sticker.py |
| `list_plugins` | `READONLY_SCOPED` | plugins.py |
| `get_plugin_info` | `READONLY_SCOPED` | plugins.py |
| `tool_search` | `READONLY_SEARCH` | tool_search.py |
| `sleep` | `CONTROL_PLANE` | sleep.py |
| `enter_worktree` | `CONTROL_PLANE` | worktree.py |
| `exit_worktree` | `CONTROL_PLANE` | worktree.py |
| `structured_output` | `READONLY_SEARCH` | structured_output.py |

### 4.15 Config / System Setup（`config.py:238`, `org_setup.py:29`）

| 工具 | ApprovalClass |
|---|---|
| `system_config` | `CONTROL_PLANE` + owner_only |
| `setup_organization` | `CONTROL_PLANE` + owner_only |

### 4.16 Agent Tool（`agent.py:32-39`）

| 工具 | ApprovalClass |
|---|---|
| `delegate_to_agent` | `CONTROL_PLANE` |
| `delegate_parallel` | `CONTROL_PLANE` |
| `spawn_agent` | `CONTROL_PLANE` |
| `create_agent` | `CONTROL_PLANE` + owner_only |
| `task_stop` | `CONTROL_PLANE` |
| `send_agent_message` | `MUTATING_SCOPED` |

### 4.17 Agent Package / Agent Hub / Skill Store

| 工具 | ApprovalClass | + owner_only |
|---|---|:---:|
| `export_agent` | `MUTATING_SCOPED` | |
| `import_agent` | `CONTROL_PLANE` | ✓ |
| `list_exportable_agents` | `READONLY_SCOPED` | |
| `inspect_agent_package` | `READONLY_SCOPED` | |
| `batch_export_agents` | `MUTATING_SCOPED` | |
| `search_hub_agents` | `READONLY_SEARCH` | |
| `install_hub_agent` | `CONTROL_PLANE` | ✓ |
| `publish_agent` | `NETWORK_OUT` | ✓ |
| `get_hub_agent_detail` | `READONLY_SEARCH` | |
| `search_store_skills` | `READONLY_SEARCH` | |
| `install_store_skill` | `CONTROL_PLANE` | ✓ |
| `get_store_skill_detail` | `READONLY_SEARCH` | |
| `submit_skill_repo` | `NETWORK_OUT` | ✓ |

### 4.18 PowerShell / OpenCLI / CLI-Anything（条件注册）

| 工具 | ApprovalClass | 注册条件 |
|---|---|---|
| `run_powershell` | `EXEC_CAPABLE`* | Windows only；同 run_shell 的 `_refine` 升降 |
| `opencli_list` | `READONLY_SCOPED` | opencli installed |
| `opencli_run` | `EXEC_CAPABLE`* | |
| `opencli_doctor` | `READONLY_SCOPED` | |
| `cli_anything_discover` | `READONLY_SEARCH` | cli-anything-* installed |
| `cli_anything_run` | `EXEC_CAPABLE`* | |
| `cli_anything_help` | `READONLY_SCOPED` | |

### 4.19 Desktop（`desktop.py:23-33`，仅 Windows）

所有 `desktop_*` 默认 `INTERACTIVE`（IM 渠道下应 deny）：
`desktop_screenshot`, `desktop_find_element`, `desktop_click`, `desktop_type`, `desktop_hotkey`, `desktop_scroll`, `desktop_window`, `desktop_wait`, `desktop_inspect`

### 4.20 工具总数估算

按 §4.1-§4.19 累加：~125 个内置工具明确分类；Skill/MCP/Plugin 工具数量动态。

---

### 4.21 开发者新增内置工具的 Cookbook

> 本节是给开发者（含 AI coding agent）的"新增内置工具"操作手册。如果你看到 CI 错误信息或启动 WARN 提到本节，按这里走就能修复。

#### 4.21.1 ApprovalClass 不是白名单

**关键观念**：ApprovalClass **不是"哪些工具被允许"的白名单**，是**风险分类标签**。

类比超市商品分类（食品 / 药品 / 危险品）：超市不查"商品在不在白名单"，而是查"商品属于哪类，按哪类规则结账"。同理 PolicyEngineV2 不查"工具是否被允许"，而是问：

> "这工具属于哪类风险？根据当前 confirmation_mode + session_role，这类该 allow / ask / deny？"

**11 类完整定义**见 §4 各小节。新工具落进哪一类决定它的默认行为。

#### 4.21.2 4 个方案，按"懒到勤"

##### 方案 A（最懒）：什么都不做 — 让启发式自动分类

ApprovalClassifier 会按工具名前缀启发式归类：

| 工具名前缀 | 自动归到 |
|---|---|
| `read_` `list_` `get_` `view_` | `READONLY_GLOBAL` |
| `search_` `find_` `grep` `glob` | `READONLY_SEARCH` |
| `write_` `edit_` `create_` `move_` `rename_` `update_` | `MUTATING_SCOPED`（跨盘自动升 `MUTATING_GLOBAL`）|
| `delete_` `uninstall_` `remove_` `drop_` | `DESTRUCTIVE` |
| `run_` `execute_` `spawn_` `kill_` | `EXEC_CAPABLE` |
| `schedule_` `cron_` `system_` `evolution_` `switch_persona` `setup_organization` | `CONTROL_PLANE` |
| 其他 | `UNKNOWN`（保守 ask 一次）|

**只要工具名符合规范，0 改动**。但**不推荐**纯靠启发式（启动会 WARN）。

##### 方案 B（**推荐 99% 场景**）：在 `agent.py` 注册时声明

新工具属于现有 handler（如 filesystem）→ 在 [`core/agent.py:_init_handlers`](../src/openakita/core/agent.py) 找到对应 `register(...)` 调用，加 `tool_classes={...}`：

```python
self.handler_registry.register(
    "filesystem", create_filesystem_handler(self),
    tool_classes={
        "read_file": ApprovalClass.READONLY_GLOBAL,
        "write_file": ApprovalClass.MUTATING_SCOPED,
        # ... 既有声明 ...
        "my_new_tool": ApprovalClass.MUTATING_SCOPED,  # ← 新增这一行
    },
)
```

**为什么推荐**：
- 一处声明，权威（不依赖工具名前缀，命名灵活）
- Code review 立刻能看到风险等级
- CI 测试会强制要求每个工具都在这或方案 C 里

##### 方案 C（新模块）：handler 类自带 `TOOL_CLASSES`

新建 handler 文件时，handler 自治：

```python
class MyHandler:
    TOOLS = ["my_tool_1", "my_tool_2"]
    TOOL_CLASSES = {  # 与 TOOLS 平级，register() 自动读
        "my_tool_1": ApprovalClass.MUTATING_SCOPED,
        "my_tool_2": ApprovalClass.READONLY_GLOBAL,
    }
```

`agent.py:_init_handlers` 注册时不传 `tool_classes` 参数也 OK（registry 自动从 handler 类读 `TOOL_CLASSES`）。适合独立模块、不污染 `agent.py`。

##### 方案 D（极少数）：参数依赖分类 → 在 classifier 加 refine

如 `write_file` 写 workspace 内/外是不同风险。修改 [`policy_v2/classifier.py:_refine_with_params`](../src/openakita/core/policy_v2/classifier.py) 加分支：

```python
def _refine_with_params(self, base, tool, params, ctx):
    # 既有：write_file 跨盘升级
    if base == ApprovalClass.MUTATING_SCOPED:
        path = params.get("path")
        if path and not _is_inside(path, ctx.workspace):
            return ApprovalClass.MUTATING_GLOBAL
    
    # 新增你的 refine 逻辑：
    if tool == "my_new_tool":
        if params.get("danger_flag"):
            return ApprovalClass.DESTRUCTIVE
    
    return base
```

仅在标准 ApprovalClass 不足以表达运行时差异时才用。同时**必须**在 `tests/unit/test_classifier.py` 加 case 覆盖 refine 路径。

#### 4.21.3 决策树（10 秒判断用哪个方案）

```
是新工具吗？
  ├─ 是
  │   ├─ 工具名前缀符合 §4.21.2 表？
  │   │   ├─ 是 → 方案 A（什么都不做，但启动会 WARN）
  │   │   └─ 否 → 必须用方案 B 或 C（CI 会拦截）
  │   ├─ 属于现有 handler？
  │   │   ├─ 是 → 方案 B（agent.py 加 tool_classes 一行）
  │   │   └─ 否 → 方案 C（新 handler 类自带 TOOL_CLASSES）
  │   └─ 行为强依赖参数？→ 加方案 D（classifier refine 分支）
  └─ 改现有工具
      ├─ 改了工具名 → 同步更新 TOOLS + TOOL_CLASSES（CI 会自动拦截不同步）
      ├─ 改了行为风险等级 → 改对应 ApprovalClass + 加迁移说明
      └─ 改了参数 → 看 _refine_with_params 是否需要更新
```

#### 4.21.4 Skill / MCP / 插件工具不在此列

它们**不需要改 OpenAkita 代码**：

| 第三方 | 自报 ApprovalClass 的方式 |
|---|---|
| Skill | SKILL.md frontmatter 加 `risk_class: readonly_global`（默认 `trust_level=default`，与启发式取严格度大者；用户在 SkillView 标 `trusted` 后才完全采信）|
| MCP | MCP server 的 `tool.annotations` 加 `risk_class`（MCP 协议 2024-11+ 支持）|
| Plugin | manifest 声明 + `trusted_tool_policy` 注册（`mutates_params` 字段强制审计）|

#### 4.21.5 自检清单（commit 前过一遍）

```
□ TOOLS 列表已加新工具名
□ ApprovalClass 已通过方案 A/B/C 确定
□ 行为依赖参数 → classifier refine 已加（仅复杂工具）
□ pytest tests/unit/test_classifier.py 全绿
□ pytest tests/unit/test_classifier_completeness.py 全绿（这个会自动扫所有工具）
□ 启动后没有 [Policy] Tool 'xxx' has no ApprovalClass 的 WARN
□ 不需要改 POLICIES.yaml
□ 不需要改 AGENTS.md
□ 不需要数据迁移
```

#### 4.21.6 常见错误

| 错误 | 修复 |
|---|---|
| CI red `unclassified tools: ['my_tool']` | 在方案 B 或 C 里声明 ApprovalClass |
| 启动 WARN `Tool 'my_tool' falling back to UNKNOWN` | 同上 |
| 新工具调用每次都 ask | ApprovalClass 是 `UNKNOWN` 或被启发式归到 `UNKNOWN` → 显式声明 |
| 改了工具名忘改 TOOL_CLASSES | CI completeness test 会拦截 |
| 第三方 Skill 工具不被信任 | 用户在 SkillView 标该 Skill 为 `trusted`，或 SKILL.md 声明 `risk_class` |

#### 4.21.7 强制护栏（无法绕过）

新增工具的 4 层护栏（实施在 Commit 19）：

1. **Cursor rule**（`.cursor/rules/add-internal-tool.mdc`）：编辑 `tools/handlers/*.py` 或 `core/agent.py` 时 IDE 自动注入提示（仅 Cursor 用户）
2. **handler 文件顶部 docstring**：30+ 个 handler 文件统一 6 行 checklist 块（任何 AI read 该文件就看到）
3. **`register()` 启动 WARN**：缺 ApprovalClass 且不匹配启发式前缀 → 启动日志刺眼 WARN
4. **CI test_classifier_completeness**：`pytest` 会扫所有注册的工具是否有显式分类（不算启发式），缺一个 → 红灯 + 错误信息直接贴本节路径

**"AGENTS.md 不动"** —— 新增工具是低频操作，不应该污染每次对话的 system prompt。护栏走精准触发载体（IDE / 文件 docstring / 运行时 / CI）。

---

## 5. 删旧 `policy.py` / `permission.py` 时必须 re-export 的符号清单

> 来源：subagent 全仓 grep。这些是**外部代码已经 import** 的符号，删主体后必须保留薄壳 re-export，否则启动时 ImportError。

### 5.1 `core/policy.py` 薄壳必须 export

**Public 符号**（被 `chat.py` / `config.py` / `channels/*` / `cli/*` / `audit_logger.py` / `checkpoint.py` / `security_actions.py` / `tool_executor.py` / `reasoning_engine.py` / `agent.py` / `permission.py` / 各 Skill handler import）：

```python
get_policy_engine()
reset_policy_engine()
PolicyDecision           # alias to PolicyDecisionV2
PolicyResult
Zone                     # 旧 enum
OpType
ConfirmationConfig       # 配置 dataclass（部分测试 import）
SelfProtectionConfig     # 同上
```

**Private 符号**（被 `config.py` 和 `tests/e2e/test_p0_regression.py` 直接 import — **不能删**）：

```python
_DEFAULT_BLOCKED_COMMANDS    # config.py:1559 line 用作默认值
_default_forbidden_paths     # config.py:1478
_default_protected_paths     # config.py:1466
_default_controlled_paths    # tests/e2e/test_p0_regression.py
_ZONE_OP_MATRIX              # tests/e2e/test_p0_regression.py
_CRITICAL_RISK_SHELL_PATTERNS  # 复用给 policy_v2/shell_risk.py（迁移源）
_HIGH_RISK_SHELL_PATTERNS
_MEDIUM_RISK_SHELL_PATTERNS
```

**注意**：[`src/openakita/channels/policy.py`](../src/openakita/channels/policy.py)（IM 群组 ACL，含 `GroupPolicyConfig`）是**完全不同的文件**，**不动**。Commit 8 在删 `core/policy.py` 主体时严禁误删。

### 5.2 `core/permission.py` 薄壳必须 export

```python
check_permission()           # 被 tool_executor.py 1170-1181 调用
PermissionDecision           # 被 tool_executor.py TYPE_CHECKING import
EDIT_TOOLS                   # 被 reasoning_engine.py 293-307 import
READ_TOOLS                   # 同上
PLAN_MODE_RULESET            # 被 tests/orgs/* import
ASK_MODE_RULESET             # 同上
COORDINATOR_MODE_RULESET     # 被 tests/orgs/test_org_coordinator_delegation.py 21-26 import
disabled()                   # 被 reasoning_engine + tests/orgs/* import
check_mode_permission()      # 被 tests/unit/test_mode_tool_policy.py import
Ruleset                      # OpenCode 风格 dataclass
```

### 5.3 `core/security_actions.py` 不删（保留独立模块）

被 `api/routes/config.py` 和 `api/routes/chat.py` 直接调用，与 PolicyEngine 解耦。Commit 8 仅修改其内部对 `get_policy_engine` 的调用为 v2，外部接口不变。

### 5.4 `core/trusted_paths.py` 不删（保留独立模块）

[`agent.py:126`](../src/openakita/core/agent.py) + [`api/routes/chat.py:22`](../src/openakita/api/routes/chat.py) import 其 `consume_session_trust` / `is_trusted_workspace_path` / `grant_session_trust`。Commit 8 修内部 bug + 加"过期真删除"逻辑，接口不变。

### 5.5 `core/risk_intent.py` 不删（保留独立模块）

[`agent.py:116, 807`](../src/openakita/core/agent.py) + [`chat.py:220-224`](../src/openakita/api/routes/chat.py) import 其 `RiskIntentResult`, `RiskLevel`, `TargetKind`, `classify_risk_intent`, `derive_authorized_intent`。Commit 5 让 RiskGate 调用 `evaluate_message_intent` 而不是这些函数，但 risk_intent.py 本身保留（仍是分类源）。

---

## 6. 外部依赖图（哪些文件 import 了即将重构的模块）

### 6.1 `from openakita.core.policy import` / `from .policy import`

| 文件 | 行号 | 符号 |
|---|---|---|
| [`api/routes/chat.py`](../src/openakita/api/routes/chat.py) | 399-401 | `get_policy_engine` → cleanup_session |
| [`api/routes/config.py`](../src/openakita/api/routes/config.py) | 1466, 1478, 1516, 1528, 1559, 1605, 1618-1639, 1708-1710, 1789, 1819-1821, 1869 | **`reset_policy_engine`, `_default_forbidden_paths`, `_default_protected_paths`, `_DEFAULT_BLOCKED_COMMANDS`, `get_policy_engine`** |
| [`channels/adapters/feishu.py`](../src/openakita/channels/adapters/feishu.py) | 1090-1092 | `get_policy_engine` → resolve_ui_confirm |
| [`channels/adapters/telegram.py`](../src/openakita/channels/adapters/telegram.py) | 698-700 | 同上 |
| [`channels/gateway.py`](../src/openakita/channels/gateway.py) | 4696-4703 | `get_policy_engine` (IM streaming) |
| [`cli/stream_renderer.py`](../src/openakita/cli/stream_renderer.py) | 303-306 | `get_policy_engine` (CLI confirm) |
| [`core/agent.py`](../src/openakita/core/agent.py) | 861-863, 2412-2414, 5709-5711 | `get_policy_engine` |
| [`core/audit_logger.py`](../src/openakita/core/audit_logger.py) | 111-113 | **`get_policy_engine` 在 init 时调用**（启动顺序关键）|
| [`core/checkpoint.py`](../src/openakita/core/checkpoint.py) | 248-250 | 同上 |
| [`core/permission.py`](../src/openakita/core/permission.py) | 295-297 | `get_policy_engine` |
| [`core/reasoning_engine.py`](../src/openakita/core/reasoning_engine.py) | 4380-4383, 4738-4742 | `PolicyDecision`, `PolicyResult`, `get_policy_engine`, `assert_tool_allowed`, `wait_for_ui_resolution` |
| [`core/security_actions.py`](../src/openakita/core/security_actions.py) | 11-13, 18-27, 38-42, 53-55 | `get_policy_engine` |
| [`core/tool_executor.py`](../src/openakita/core/tool_executor.py) | 805-810 | `get_policy_engine`, `mark_confirmed` |
| [`tools/handlers/skills.py`](../src/openakita/tools/handlers/skills.py) | 289-291, 820-822, 908-910, 986-988 | `get_policy_engine` (skill tool allowlists) |

### 6.2 `from openakita.core.permission import`

| 文件 | 行号 | 符号 |
|---|---|---|
| [`core/reasoning_engine.py`](../src/openakita/core/reasoning_engine.py) | 293-307 | Ruleset 相关 + mode ruleset helpers |
| [`core/tool_executor.py`](../src/openakita/core/tool_executor.py) | 24, 1176-1181 | `PermissionDecision`, `check_permission` |
| [`tests/orgs/test_org_coordinator_delegation.py`](../tests/orgs/test_org_coordinator_delegation.py) | 21-26 | `COORDINATOR_MODE_RULESET`, `disabled` |

### 6.3 `from openakita.core.security_actions import`

| 文件 | 行号 | 符号 |
|---|---|---|
| [`api/routes/config.py`](../src/openakita/api/routes/config.py) | 1085, 1102, 1723-1727, 1884-1920 | allowlist helpers, death-switch wiring |
| [`api/routes/chat.py`](../src/openakita/api/routes/chat.py) | 21, 263-268 | `execute_controlled_action`, `maybe_broadcast_death_switch_reset`, `maybe_refresh_skills` |

### 6.4 `from openakita.core.trusted_paths import`

| 文件 | 行号 | 符号 |
|---|---|---|
| [`core/agent.py`](../src/openakita/core/agent.py) | 126 | `consume_session_trust`, `is_trusted_workspace_path` |
| [`api/routes/chat.py`](../src/openakita/api/routes/chat.py) | 22, 162-165 | `grant_session_trust` |

### 6.5 `from openakita.core.risk_intent import`

| 文件 | 行号 | 符号 |
|---|---|---|
| [`core/agent.py`](../src/openakita/core/agent.py) | 116, 807 | `RiskIntentResult`, `RiskLevel`, `TargetKind`, `classify_risk_intent`, `AuthorizedIntent` (lazy) |
| [`api/routes/chat.py`](../src/openakita/api/routes/chat.py) | 220-224 | `derive_authorized_intent` (feature flag) |

### 6.6 `orgs/runtime.py` patches `execute_tool_with_policy`

[`orgs/runtime.py`](../src/openakita/orgs/runtime.py) 对 [`tool_executor.execute_tool_with_policy`](../src/openakita/core/tool_executor.py) 做了 monkey-patch（org 委派路径）。Commit 4 必须保持 `execute_tool_with_policy` 的**函数签名 + 返回类型 + 异常类型**完全不变，否则 org 委派挂掉。

---

## 7. 现有 `identity/POLICIES.yaml` 完整 Schema（v1）

```yaml
security:
  enabled: true
  zones:
    enabled: true
    workspace: [${CWD}]
    controlled: []
    protected: [C:/Program Files/**, C:/Windows/**, /etc/**, /usr/**, /System/**, ...]
    forbidden: [~/.ssh/**, ~/.gnupg/**, /etc/shadow, ...]
    default_zone: workspace
  confirmation:
    enabled: true
    mode: yolo               # yolo / smart / cautious
    timeout_seconds: 60
    default_on_timeout: deny
    confirm_ttl: 120.0
  command_patterns:
    enabled: false
    custom_critical: []
    custom_high: []
    excluded_patterns: []
    blocked_commands: [reg, regedit, netsh, schtasks, sc, wmic, bcdedit, shutdown, taskkill]
  checkpoint:
    enabled: true
    max_snapshots: 50
    snapshot_dir: data/checkpoints
  self_protection:
    enabled: false
    protected_dirs: [data/, identity/, logs/, src/]
    audit_to_file: true
    audit_path: data/audit/policy_decisions.jsonl
    death_switch_threshold: 3
    death_switch_total_multiplier: 3
  sandbox:
    enabled: false
    backend: auto
    sandbox_risk_levels: [HIGH]
    exempt_commands: []
    network: { allow_in_sandbox: false, allowed_domains: [] }
  user_allowlist:
    commands: []
    tools: []
```

**v2 新 schema 见 plan §7**。迁移规则（在 [`policy_v2/loader.py`](../src/openakita/core/policy_v2/loader.py) 实现）：

| 旧字段 | 新字段 | 迁移规则 |
|---|---|---|
| `confirmation.mode = yolo` | `confirmation_mode = trust` | 自动 |
| `confirmation.mode = smart` | `confirmation_mode = default` | 自动 |
| `confirmation.mode = cautious` | `confirmation_mode = strict` | 自动 |
| `self_protection.protected_dirs` | `safety_immune.paths` | 合并 + 精细化（plan §4 9 类）|
| `zones.protected` + `zones.forbidden` | 合并进 `safety_immune.paths` | 启动时 union |
| `zones.workspace` | 保留 | 给 ApprovalClassifier 用（"在不在 workspace"）|
| `zones.default_zone` | 废弃 | v2 不依赖 zone |
| `user_allowlist` | 保留不变 | 用户数据 |
| `command_patterns.custom_*` / `excluded_patterns` / `blocked_commands` | 保留 | 给 step 1c run_shell handler 自查 |
| `checkpoint`, `sandbox` | 保留 | metadata 触发 |
| `self_protection.death_switch_*` | `death_switch.consecutive_limit` / `total_limit` | 字段重命名 |

---

## 8. 现有 SSE Confirm 协议字段（实测）

> 第二轮调研（R2-A）发现：plan v1 草稿误以为现有 SSE 只支持 2 选项需要扩展到 4 选项。**实际现有协议已经支持 5 选项**。v2 仅做"标准化命名 + 新增向后兼容字段"。

```json
{
  "type": "security_confirm",
  "tool": "write_file",
  "tool_name": "write_file",
  "args": {...},
  "id": "tool-call-123",
  "confirm_id": "tool-call-123",
  "call_id": "tool-call-123",
  "reason": "...",
  "risk_level": "high",
  "needs_sandbox": false,
  "timeout_seconds": 60,
  "default_on_timeout": "deny",
  "options": ["allow_once", "allow_session", "allow_always", "deny"]
}
```

`needs_sandbox=true` 时 `options` 末尾追加 `"sandbox"`。

**v2 新增字段**（向后兼容）：

```json
{
  "approval_class": "mutating_global",
  "decision_chain": [...],   // 默认不带，仅 dev mode；详情按需 GET /api/policy/decision/{id}
  "policy_version": 2
}
```

**v2 全新事件类型**：`tool_intent_preview`（plan §8.4）+ `pending_approval_created` + `pending_approval_resolved` + `security_confirm_already_resolved` + `policy_config_reloaded` + `policy_config_reload_failed`。

---

## 9. 现有 IM 适配器与 Owner 判断现状

### 9.1 适配器列表

| 渠道 | 文件 | 现有 owner_user_id 判断 |
|---|---|---|
| Telegram | [`channels/adapters/telegram.py`](../src/openakita/channels/adapters/telegram.py) | 部分（`OWNER_USER_ID` env） |
| Feishu | [`channels/adapters/feishu.py`](../src/openakita/channels/adapters/feishu.py) | 部分 |
| DingTalk | [`channels/adapters/dingtalk.py`](../src/openakita/channels/adapters/dingtalk.py) | 缺失 |
| WeWork (WS) | [`channels/adapters/wework_ws.py`](../src/openakita/channels/adapters/wework_ws.py) | 缺失 |
| WeChat | [`channels/adapters/wechat.py`](../src/openakita/channels/adapters/wechat.py) | 缺失 |
| QQ Official | [`channels/adapters/qq_official.py`](../src/openakita/channels/adapters/qq_official.py) | 缺失 |
| OneBot | [`channels/adapters/onebot.py`](../src/openakita/channels/adapters/onebot.py) | 缺失 |

### 9.2 v2 统一接入点

Commit 6 在每个适配器的"派发消息前"统一加：

```python
is_owner = (sender_user_id == settings.owner_user_id_for_channel(channel))
session.metadata["is_owner"] = is_owner
```

PolicyContext.from_session(session) 自动读取此字段。**默认 `is_owner=True`**（CLI/桌面）；IM 必须显式判断。

### 9.3 与 IM 群组 ACL 的关系（R5-22）

[`api/routes/im.py`](../src/openakita/api/routes/im.py) 的 `_GROUP_POLICY_PATH = data/sessions/group_policy.json` 是**独立的 IM 群组级 ACL**（"哪些群能用哪个 mode"），与 `owner_only` 是**AND 关系**：

- group ACL 通过 + owner_only 通过 → 执行
- 任意一层 deny → deny
- 不冲突，但 SecurityView UI 上要分两个区块展示

---

## 10. Handler 注册位置（重大设计简化）

### 10.1 真实位置

**所有 handler 注册在 [`core/agent.py:2215-2331`](../src/openakita/core/agent.py) 的 `_init_handlers()` 一处**，30+ 个 `registry.register("name", create_xxx_handler(self))` 调用集中：

```python
self.handler_registry.register("filesystem", create_filesystem_handler(self))
self.handler_registry.register("memory", create_memory_handler(self))
# ... 共 30+ 个
```

`tool_names` 默认从 handler 实例的 `.TOOLS` class attribute 自动读（见 `tools/handlers/__init__.py:53-78` 的 `register` 实现）。

### 10.2 v2 修改面（仅 2 个文件）

1. [`tools/handlers/__init__.py`](../src/openakita/tools/handlers/__init__.py) `SystemHandlerRegistry.register()` 加 `tool_classes: dict[str, ApprovalClass]` 可选参数
2. [`core/agent.py:_init_handlers`](../src/openakita/core/agent.py) 集中处的 30+ 个 register 调用补 `tool_classes={...}`（按 §4 表填）

**没有**需要修改 30 个 handler 文件。这是 R5-6 发现的重大设计简化。

---

## 11. 现有持久化文件清单

| 路径 | 写入者 | 用途 | v2 影响 |
|---|---|---|---|
| `identity/POLICIES.yaml` | `api/routes/config.py:write_security_config` | 安全配置 | §7 schema 升级 + deep-merge |
| `identity/SOUL.md` / `AGENT.md` / `USER.md` | identity API | agent identity | safety_immune 保护 |
| `identity/SYSTEM_TASKS.yaml` | **新增**（C15）| system 任务白名单 | §17.2 |
| `data/audit/policy_decisions.jsonl` | `audit_logger.AuditLogger.log` | 决策审计 | C17 改异步批量 + hash chain |
| `data/audit/plugin_param_modifications.jsonl` | **新增**（C10）| 插件改 params 审计 | §10 |
| `data/audit/evolution_decisions.jsonl` | **新增**（C15）| evolution 决策审计 | §17.1 |
| `data/checkpoints/` | `CheckpointManager` | 文件快照 | DESTRUCTIVE/MUTATING_GLOBAL 触发 |
| `data/sessions/sessions.json` (+ `.bak`) | `SessionManager` | 会话状态 | C8 加 session_role/confirmation_mode 字段 |
| `data/sessions/group_policy.json` | `api/routes/im.py` | IM 群组 ACL | 不动（独立）|
| `data/scheduler/tasks.json` | `TaskScheduler` | 计划任务定义 | C12 加 6 个字段 |
| `data/scheduler/executions.json` (jsonl) | `TaskScheduler._append_execution` | 执行历史 | C12 加 awaiting_approval status |
| `data/scheduler/pending_approvals.json` | **新增**（C12）| 待审批队列 | §14.5 |
| `data/scheduler/locks/exec_*.json` | **新增**（C17）| 进程崩溃恢复 | §19.1 |
| `data/scheduler/pending_approvals_archive_YYYYMM.jsonl` | **新增**（C12）| 7 天后归档 | §14.11 |
| `data/plugin_state.json` | `plugins/state.py`（_SCHEMA_VERSION = 2）| 插件状态 | 不动 |
| `data/llm_endpoints.json` | endpoint API | LLM 端点 | safety_immune 保护 |
| `data/users/*` | user manager | 用户档案 | safety_immune 保护 |
| `.openakita/system_tasks.lock` | **新增**（C15）| SYSTEM_TASKS.yaml hash 校验 | §17.2 防篡改 |

---

## 12. 18 commit 对应表（plan ↔ 调研 ↔ 实施进度）

> **实施顺序与原 plan 略有调整**：plan 原 C7（YAML schema）提前到实施 C4，
> 因为 C5+ 的 PolicyEngineV2 接线（owner_only / approval_classes / unattended）
> 需要 PolicyConfigV2 作为输入。其余 commit 顺序不变。

| 实施 # | 标题 | 状态 | 调研依据 |
|---|---|---|---|
| C0 | 调研落档（本文）| ✅ Done | 本文 |
| C1 | `policy_v2/` 模块骨架（enums / models / matrix / exceptions / context）| ✅ Done | §3.1 R1-1, §3.2 R2-13 |
| C2 | ApprovalClassifier + SystemHandlerRegistry 扩展 | ✅ Done | §4 + R2-5 |
| C3 | PolicyEngineV2 双入口 + 12 步 + zones/shell_risk | ✅ Done | §3.1 R1-1, §3.2 R2-8/10/13 |
| C4 | PolicyConfigV2（schema + migration + loader）| ✅ Done | §7 完整 schema + R1-5 deep-merge |
| C5 | PolicyEngineV2 接入 PolicyConfigV2（owner_only / approval_classes overrides / shell_risk customs / unattended 5 策略 / safety_immune 配置化）+ engine 的 stub step 实装 + boundary coercion | ✅ Done | §3.2 R2-1 + R2-10 + 11 步链落地 |
| C6 | tool_executor 切 v2 + reasoning_engine 决策切 v2 + orgs/runtime 兼容 + `_path_under` glob bug 修复 | ✅ Done | §3.1 R1-6 + §6.6 + R2-2 |
| C7 | agent.py RiskGate 切 v2 + ContextVar wire + handler.TOOL_CLASSES (30+) + explicit_lookup 注入 | ✅ Done | §3.2 R2-1 + R2-10 + R2-12 |
| C8a | safety_immune 9 类 + OwnerOnly 配置驱动 + switch_mode 真生效 + consume_session_trust 真删 + IM 前缀 SSE bug | ✅ Done | §2.3 + §5 + R5-22 |
| C8b-1 | v2 补能：UserAllowlist + SkillAllowlist + DeathSwitch + step 9/10 实装 | ✅ Done | §6.6 + 「C8b-1 实施记录」 |
| C8b-2 | 配置常量与 SecurityConfig 子段读取迁移：`policy_v2/defaults.py` + `reset_policy_v2_layer` + audit_logger/checkpoint 改读 v2 | ✅ Done | §6.6 + 「C8b-2 实施记录」 |
| C8b-3..5 | （未启动）UI confirm 切换 / RiskGate 删除 / PolicyEngine class 删除 | ⏳ Pending | §6.6 + §10 + 「C8b 粒度化执行计划」|
| C9a | SecurityView v2 适配（approval_class badge + IM owner UI + dry-run preview） | ✅ Done | §8 + R5-20 |
| C9b | UI confirm bus 抽出（`core/ui_confirm_bus.py`），让 C8b 能安全删 v1 | ✅ Done | §6.6 + R5-22 |
| C9c | tool_intent_preview / pending_approval_* / policy_config_reloaded SSE 事件（推迟到 C12 一起做） | ⏳ Deferred | §8 + R2-11 |
| C10 | Hook 来源分层 + Trusted Tool Policy + plugin manifest 桥接 | ⏳ Pending | §3.2 R2-12 + R5-7 |
| C11 | 全量回归 + 25 项手测 + 性能 SLO | ⏳ Pending | plan §13.5 + R5-18/19 |
| C12 | 计划任务/无人值守审批 + DeferredApprovalRequired + pending_approvals | ⏳ Pending | §2.1 + R3 |
| C13 | 多 agent confirm 冒泡 + delegate_chain 透传 | ⏳ Pending | R4-1/2/3/4 + R5-16 |
| C14 | Headless 入口统一（CLI / HTTP / Webhook / stdin）| ⏳ Pending | R4-5/6/7/8 |
| C15 | Evolution / system_task / Skill-MCP trust_level | ⏳ Pending | R4-9/10/11/12/13 + R5-21 |
| C16 | Prompt injection + YAML 严格校验 + audit 防篡改 | ⏳ Pending | R4-14/15 + R5-17 |
| C17 | Reliability（lock 文件 / 启动扫描 / Last-Event-ID / health probe）| ⏳ Pending | R4-16/17/18/19 + R5-8/12 |
| C18 | UX + 配置完备性（hot-reload / ENV / dry-run / 5s 聚合）| ⏳ Pending | R4-20/21/22 + R5-11/20 |
| **C19** | **开发者新增工具 4 层护栏（依赖 C2/C8/C11，在 C12 之前实施以护卫 C12-C18）** | ⏳ Pending | §4.21 cookbook + §12.5 |

---

## 12.5 Commit 19 设计：开发者新增工具的 4 层护栏

### 12.5.1 动机

AI coding agent（含我自己）在 OpenAkita 后续迭代中会频繁加内置工具。如果新工具不在 ApprovalClass 体系里：
- **方案 A**（启发式）会兜底，但风险等级可能失真（例：新增 `flush_database` 被启发式归到 `MUTATING_SCOPED`，实际应是 `DESTRUCTIVE`）
- 用户感受：明明已开 trust 模式，新工具仍每次 ask（启发式归到 `UNKNOWN`）
- 安全感受：明明应该 deny 的破坏性新工具被静默放行

需要"无法绕过 + 0/低 token 成本 + 精准触发"的护栏。

### 12.5.2 4 层护栏（按"触发精准度"排序）

#### 12.5.2.1 Layer-1：CI completeness test（最硬）

**位置**：`tests/unit/test_classifier_completeness.py`（新建）

```python
"""
扫描所有注册的工具，断言每个都有显式 ApprovalClass（不算启发式回退）。

新工具触发 RED 时，错误信息直接贴 docs/policy_v2_research.md §4.21 路径。
"""
def test_all_registered_tools_have_explicit_approval_class():
    agent = _make_test_agent()  # 触发完整 _init_handlers
    classifier = agent.policy_engine.classifier
    
    unclassified = []
    for tool_name in agent.tool_registry.all_tool_names():
        approval_class, source = classifier.classify_with_source(tool_name)
        if source in ("heuristic_prefix", "fallback_unknown"):
            unclassified.append((tool_name, source))
    
    assert not unclassified, (
        f"以下工具缺显式 ApprovalClass 声明:\n"
        + "\n".join(f"  - {t} (source={s})" for t, s in unclassified)
        + f"\n\n请按 docs/policy_v2_research.md §4.21 选择方案 B/C/D 添加声明。"
    )
```

**触发时机**：本地 `pytest`、PR CI。
**成本**：0 token，0 运行时开销（只在测试运行时执行）。
**Bypass 难度**：必须主动跳过测试或骗过 `classify_with_source`，正常流程过不去。

#### 12.5.2.2 Layer-2：register() 启动 WARN（运行时兜底）

**位置**：`src/openakita/tools/handlers/__init__.py:register()` 内（修改）

```python
def register(self, name, handler, tool_classes=None):
    if not tool_classes and not getattr(handler, "TOOL_CLASSES", None):
        # handler 没显式声明 TOOL_CLASSES → 启发式将兜底
        for tool in getattr(handler, "TOOLS", []):
            cls, src = self._classifier_probe(tool)
            if src in ("heuristic_prefix", "fallback_unknown"):
                logger.warning(
                    "[Policy] Tool %r in handler %r has no explicit ApprovalClass "
                    "(falling back to %s via %s). See docs/policy_v2_research.md §4.21",
                    tool, name, cls.value, src,
                )
```

**触发时机**：每次 OpenAkita 启动。
**成本**：0 token，启动时一次扫描（O(N) where N=tools count，~125），可忽略。
**Bypass 难度**：开发者会主动看 WARN 日志（CI 之前的本地反馈环）。

#### 12.5.2.3 Layer-3：handler 文件 docstring（编辑时命中）

**位置**：所有 `src/openakita/tools/handlers/*.py`（30+ 文件）顶部统一加 6 行注释块。

```python
"""
Filesystem tool handler.

# ApprovalClass checklist (新增/修改工具时必读)
# 1. 在 TOOLS 列表加新工具名
# 2. 在 agent.py:_init_handlers 的 register() 调用里给 tool_classes 加新条目
#    或：在本文件类内加 TOOL_CLASSES = {...}（与 TOOLS 平级）
# 3. 行为依赖参数 → 在 policy_v2/classifier.py:_refine_with_params 加分支
# 4. 跑 pytest tests/unit/test_classifier_completeness.py 验证
# 详见 docs/policy_v2_research.md §4.21
"""
```

**触发时机**：AI / 人类 read 该 handler 文件时（编辑、修 bug、加工具都会读）。
**成本**：~6 行 × 30 文件 = 累计 ~180 行 docstring；每次 read handler 进 context ~50 tokens（按 6 行 30 chars 计算）。
**Bypass 难度**：编辑该文件就看到，可忽视但很显眼。

#### 12.5.2.4 Layer-4：Cursor rule（IDE 注入，仅 Cursor 用户）

**位置**：`.cursor/rules/add-internal-tool.mdc`（新建）

```mdc
---
description: 新增/修改 OpenAkita 内置工具时的 ApprovalClass 规范
globs:
  - "src/openakita/tools/handlers/**/*.py"
  - "src/openakita/core/agent.py"
  - "src/openakita/core/policy_v2/classifier.py"
alwaysApply: false
---

新增内置工具时必须显式声明 ApprovalClass，否则 CI red。
完整 SOP + 4 个方案见 [docs/policy_v2_research.md §4.21](mdc:docs/policy_v2_research.md)

最小改动（推荐方案 B）：
在 src/openakita/core/agent.py 的 _init_handlers 里找到对应 register()，
给 tool_classes={} 加一行：
  "my_new_tool": ApprovalClass.MUTATING_SCOPED,
```

**触发时机**：仅 Cursor IDE 用户编辑符合 globs 的文件时按需注入。
**成本**：~80 tokens × 触发次数（仅当 AI 实际编辑相关文件时）。
**Bypass 难度**：非 Cursor 用户看不到，但被 Layer-1 兜底。

### 12.5.3 不做的事（明确声明）

| 不做 | 理由 |
|---|---|
| 改 `AGENTS.md` 加 cookbook | 每次对话 system prompt 都付费，新增工具是低频，不值 |
| pre-commit hook 跑 completeness test | OpenAkita 默认无 pre-commit，避免增加新依赖；CI red 已足够 |
| 自动生成 ApprovalClass | 代码生成不可靠且会掩盖开发者思考；让开发者主动归类 |
| 强制 type checker 检查 | mypy 在本仓是 lenient，强制 strict 范围太大 |

### 12.5.4 实施清单（C19 commit 内容）

| 文件 | 操作 | 行数估计 |
|---|---|---|
| `tests/unit/test_classifier_completeness.py` | 新建 | ~60 |
| `src/openakita/tools/handlers/__init__.py` | 改 `register()` 加 WARN 逻辑 | +20 |
| `src/openakita/core/policy_v2/classifier.py` | 加 `classify_with_source()` 公开方法（返回 source 字段）| +15 |
| `src/openakita/tools/handlers/*.py` | 30+ 文件统一加 6 行 docstring 块（脚本批量）| +180（30×6）|
| `.cursor/rules/add-internal-tool.mdc` | 新建 | ~30 |
| `docs/policy_v2_research.md` §4.21 | 已在 C0 提前写入（本次）| - |

**DoD**：
- `pytest tests/unit/test_classifier_completeness.py` 全绿（含已声明的 125+ 工具）
- 启动日志可看到对应 WARN（人为漏声明一个工具时）
- 编辑 handler 文件时 Cursor rule 注入提示
- handler 文件顶部都能 grep 到 `# ApprovalClass checklist`

### 12.5.5 与其他 Commit 的依赖

- **依赖 C2**：必须先有 `ApprovalClassifier.classify_with_source()`，C19 的 test 才能跑
- **依赖 C8**：tool_classes 注入位点需要 `_init_handlers` 已切到 v2
- **顺序**：C19 实际放在 **C11 之后、C12 之前**（核心 v1→v2 切换稳定后再加开发者侧护栏）

---

## 13. 后续 Commit 实施记录（待填）

每个后续 commit 完成时，回到本节追加 1 段实施记录：

```markdown
### Cn 实施记录

- **完成日期**：YYYY-MM-DD
- **实际修改文件**：
  - <path1> (+N -M)
  - <path2> (+N -M)
- **偏离 plan 的地方**：<如果有>
- **新发现的事实**：<如果有，回到 §3 增加 R6-x 行>
- **测试结果**：pytest <count> passed; ruff 0; perf SLO 达标项数 / 总项数
- **手测验证**：手测项 <i>-<j> 完成
```

#### C0 实施记录

- **完成日期**：2026-05-13
- **实际修改文件**：
  - `docs/policy_v2_research.md` (新增，~1170 行)
- **偏离 plan 的地方**：
  - plan §9 Commit 0 仅说"含 12 处事实清单 + 4 处现存 bug"。实施时把 R2/R3/R4/R5 后续轮次共 75 项也并入（避免后续重复回查 plan）。
  - 现存 Bug 计数从 4 升到 5（plan v2 已纳入 §2.1 `execute_batch` 撒谎 bug，本文档与之对齐）。
  - 用户在 C0 收尾追加问"开发者新增工具时如何处理 ApprovalClass" → 在 C0 同 PR 内提前写入 §4.21 cookbook 与 §12.5 Commit 19 设计，避免 C19 实施时因 cookbook 缺位让 CI 错误信息指向死链。**plan 同步新增 Commit 19**（4 层护栏：CI test + register WARN + handler docstring + Cursor rule，**不动 AGENTS.md**）。
- **新发现的事实**：
  - `tools/handlers/__init__.py:53-78` 的 `register()` 默认从 handler `.TOOLS` 属性自动读 tool_names，§4 工具映射表的 30+ handler 全部使用此机制（直接读各 handler 的 `TOOLS = [...]` 即得权威列表）。
  - `desktop.py:23-33` 的 TOOLS 列表通过 module-level `DESKTOP_TOOLS` 常量赋给 `class.TOOLS`（特殊写法，C2 注入 `tool_classes` 时要兼容）。
  - C19 设计期间确认：handler `.TOOL_CLASSES` 类属性是更优的"自治声明"方式（与 `.TOOLS` 平级），register() 自动读取无需修改 agent.py。3 种声明位点（agent.py register 参数 / handler.TOOL_CLASSES / classifier refine）取严格度大者（safety-by-default）。
- **测试结果**：N/A（仅文档）
- **手测验证**：N/A
- **下一步**：C1 创建 `src/openakita/core/policy_v2/` 目录结构

#### C1 实施记录

- **完成日期**：2026-05-13
- **实际修改文件**（7 个新文件，零 v1 改动）：
  - `src/openakita/core/policy_v2/__init__.py` (+71)
  - `src/openakita/core/policy_v2/enums.py` (+118)
  - `src/openakita/core/policy_v2/exceptions.py` (+98)
  - `src/openakita/core/policy_v2/models.py` (+114)
  - `src/openakita/core/policy_v2/context.py` (+185)
  - `src/openakita/core/policy_v2/matrix.py` (+170)
  - `tests/unit/test_policy_v2_skeleton.py` (+260)
- **偏离 plan 的地方**：
  - **未创建空占位文件**（classifier.py / engine.py / zones.py 等 20 个）。理由：空 docstring 文件无信息量，让 ruff/grep 噪声变大；C2-C18 各自创建即可，每个 commit 的 diff 更聚焦。
  - **`PolicyResult` 设为 `PolicyDecisionV2` 别名**（`PolicyResult = PolicyDecisionV2`，非 subclass）。orgs/runtime.py 等外部代码 `import PolicyResult` 时仍工作，且 `is` 比较通过（测试覆盖）。
  - **`INTERACTIVE` 矩阵决策一律 ALLOW**（不论 role/mode）。原因：INTERACTIVE 包括 `ask_user` 这类与用户互动的工具，本身就是为交互而生；IM 渠道下 `desktop_*`/`browser_*` 的屏蔽由 engine 层 channel-class compatibility 检查负责，不在矩阵层（避免矩阵+渠道双责）。
  - **`UNKNOWN` 在 `DONT_ASK` 模式仍是 CONFIRM**（不下放到 ALLOW）。理由：dont_ask 是"不要打扰我"，但 UNKNOWN 一定意味着我们不知道工具风险——静默放行违反 safety-by-default。
  - **`coordinator` × `trust` 比 `agent` × `trust` 严**（CONTROL_PLANE / MUTATING_GLOBAL / EXEC_CAPABLE 仍 CONFIRM）。理由：org root coordinator 调度多个 specialist，单次 confirm 可能放行多个下游动作，应更谨慎。
- **新发现的事实**：
  - 项目惯例 enum 用 `enum.StrEnum`（Python 3.11+），不用 `class X(str, Enum)`（ruff UP042 会拦截）。已与 `core/risk_intent.py` 等保持一致。
  - 项目无 `pyproject.toml` 配置 mypy strict，类型注解用 `from __future__ import annotations` 即可（ApprovalClass 在 to_audit_dict 用 `.value` 而非 `.name`，与 `class X(StrEnum)` 行为一致）。
  - `PolicyEngineV2` 启动顺序问题（R5-4：audit_logger / checkpoint init 时调 `get_policy_engine`）暂未触及，C8 处理。本 commit 不引入新的 module-level side effect，导入 `policy_v2` 模块本身零成本（无 I/O、无单例创建）。
- **测试结果**：
  - `pytest tests/unit/test_policy_v2_skeleton.py`：22 passed
  - `pytest tests/unit/test_security.py tests/unit/test_security_permission_mode_api.py`：90 passed（v1 path 不受影响）
  - `ruff check src/openakita/core/policy_v2/ tests/unit/test_policy_v2_skeleton.py`：clean
  - 手动 import smoke：`from openakita.core import policy` + 5 个私有/公共符号导入正常
- **手测验证**：N/A（骨架 commit，无用户可见行为变化）
- **下一步**：C2 实现 `ApprovalClassifier`（5 步分类链 + classify_with_source 公开方法，§4 工具映射表是其权威源数据）

#### C2 实施记录

- **完成日期**：2026-05-13
- **实际修改文件**（4 个，1 新增 + 3 改动）：
  - `src/openakita/core/policy_v2/classifier.py` (新增, +280)
  - `src/openakita/core/policy_v2/enums.py` (+50：`strictness()` + `most_strict()` + `_STRICTNESS_ORDER`)
  - `src/openakita/core/policy_v2/__init__.py` (+5：导出 `ApprovalClassifier` / `strictness` / `most_strict`)
  - `src/openakita/tools/handlers/__init__.py` (+85 -2：`register(tool_classes=)` 参数 + `_collect_tool_classes` + `get_tool_class` + `_tool_classes` 字典 + `unregister/unmap_tool` 同步清理)
  - `tests/unit/test_classifier.py` (新增, +395，**75 个测试**)
- **偏离 plan 的地方**：
  - **不动 `agent.py` 30+ 个 register 调用**。理由：C2 阶段先确保 ApprovalClassifier + registry 接口扎实，30 个 register 调用补 `tool_classes={...}` 涉及 §4 工具映射表全量翻译，放到 C8 切换到 v2 PolicyEngine 时一起做（C8 反正要碰这些调用方）。这样 C2 commit 最小且可独立 review。
  - **`_collect_tool_classes` 实现"显式来源叠加 most_strict"**：register param + handler.TOOL_CLASSES 同时声明同一工具时取严格度大者（避免 typo 静默降级）。这超出 plan 原始描述（plan 只说"register param 优先"），是 safety-by-default 加固。
  - **加 typo WARN**：`tool_classes` 提到 TOOLS 列表外的工具名时 WARN（如开发者拼错工具名）。提前预警，避免 silent miss。
  - **顺手修 1 处 pre-existing UP037**：`handlers/__init__.py` 的 `"SystemHandlerRegistry.ConcurrencyCheck"` 引号注解。加 `from __future__ import annotations`（policy_v2 一致风格）。AGENTS.md 允许在编辑时顺手修 pre-existing lint。
- **新发现的事实**：
  - 启发式表 `update_` → MUTATING_SCOPED 与实际工具 `update_scheduled_task`（CONTROL_PLANE）冲突。docs §4.21.2 启发式表本就只是兜底，正确分类靠 explicit 声明（C8 在 §4.4 表里把 `update_scheduled_task` 标 CONTROL_PLANE）。测试 `test_update_scheduled_falls_into_mutating_not_control` 覆盖此预期。
  - `_is_inside_workspace` 跨平台路径比较：Windows 大小写不敏感，需 `lower()` 兜底；NUL 字节等无效输入应 fallthrough 到 False（保守判外 → 升级严格度）。已加测试覆盖。
  - 路径字段并非只有 `path`：`move_file` 用 `src`/`dst`，部分工具用 `source`/`target`/`file_path`。`_refine_with_params` 扫所有候选字段，**任一字段在 workspace 外即升级**（保守）。
- **测试结果**：
  - `pytest tests/unit/test_classifier.py`：75 passed
  - `pytest tests/unit/test_policy_v2_skeleton.py`：22 passed（C1 不退化）
  - `pytest tests/unit/test_security.py + test_security_permission_mode_api.py`：90 passed（v1 不受影响）
  - `pytest tests/unit/test_skill_tool_handlers.py + test_filesystem_tools.py + test_tool_executor_timeout_policy.py + tests/component/test_tool_executor.py`：90 passed（registry 改造对调用方零回归）
  - `ruff check`：clean（含修 1 处 pre-existing UP037）
- **手测验证**：N/A（C2 不暴露新用户可见行为；registry.get_tool_class 当前无生产消费者，C8 接入时再做端到端手测）
- **下一步**：C3 实现 `PolicyEngineV2`（双入口 `evaluate_tool_call` + `evaluate_message_intent` + 12 步决策链 + `shell_risk.py` 落地 run_shell 类的 refine 第二阶段）

##### C2 复审（同日完成，100 项硬核审查）

5 维度系统审查后追加 1 项严格化修复 + 4 项补台测试：

**1. 完整性**：对照 docs §3 全部 R 项 + §12 commit 表，C2 范围全部 close。R2-5 (`needs_sandbox`/`shell_risk_level`) 标 C2/C3，C2 留接口（不扩展返回类型，避免半成品字段污染）；C3 实现 `shell_risk.py` 时通过新增 `classify_full()` 方法平滑扩展，保持 `classify_with_source()` 签名稳定。

**2. 架构**：
- 无循环依赖（实测 `import openakita.tools.handlers` 不触发 `policy_v2` 加载，lazy import 在 `register()` 内）
- handlers/__init__.py 模块加载零 v2 副作用，`HandlerFunc` bound-method 模式与 v1 完全一致
- 4 callback 接口设计为 SKILL/MCP/PLUGIN 接入预留位（C10/C15）

**3. 正确性 — 1 处严格化**：原实现里"`tool_classes` 含 typo（不在 TOOLS 列表的工具名）"会**仍写入** `_tool_classes`（仅 WARN）。隐患：将来某 plugin 注册同名工具时会**意外继承**这个孤立 class，造成语义错乱。**修复：WARN + 丢弃**（保持 `_tool_classes ⊆ _tool_to_handler` 不变量），更新对应测试。

**4. 已知限制（非 bug，已加测试冻结行为）**：
- **classifier cache 不自动随 registry mutation 失效**。OpenAkita 启动时一次注册，运行时不变 → 实际无影响。plugin 动态注册时必须显式调 `classifier.invalidate(tool)`。C10 plugin 接入时设计自动同步机制（registry 触发 hook 通知 classifier）。
- **重复 register 同一 handler_name 不能"降级"风险**：第二次声明的低风险 ApprovalClass 被 most_strict 覆盖。这是 safety-by-default 设计，但开发者修 typo（第一次错标 DESTRUCTIVE → 想改成 READONLY）时无法直接撤回，需先 unregister。
- 4 个补台测试：`test_repeated_register_takes_strict` / `test_class_value_can_be_str_alias`（StrEnum 字符串等价）/ `TestCacheStaleness::test_unregister_does_not_auto_invalidate` / `TestCacheStaleness::test_invalidate_then_reclassify_picks_up_new_state`。

**5. 兼容性 — 实测零回归**：
- C2 stash 前：1083 passed, 1 failed（`test_org_setup_tool::test_delete_nonexistent` — pre-existing test-isolation issue）
- C2 stash 后：1083 passed, 1 failed（**完全相同**）
- 单跑 `test_delete_nonexistent` PASS，与 C2 无关
- 关键 v1 测试集（security + permission_mode + skill_tool_handlers + filesystem_tools + tool_executor + browser_handler + skeleton + classifier）共 **289 passed**

**最终测试规模**：
- C2 测试 79 个（原 75 + 复审新增 4）：`pytest tests/unit/test_classifier.py` → 79 passed
- C1 测试 22 个不退化
- v1 关键集合 188 个不受影响

#### C3 实施记录

- **完成日期**：2026-05-13
- **实际修改/新增文件**（9 个，5 新增 + 4 改动）：
  - `src/openakita/core/policy_v2/zones.py`（新增, +71）：`is_inside_workspace` 从 `classifier.py` 提升为公共 API；新增 `candidate_path_fields` / `all_paths_inside_workspace` 复用给 engine + classifier
  - `src/openakita/core/policy_v2/shell_risk.py`（新增, +205）：`ShellRiskLevel` enum + 迁移 v1 的 CRITICAL/HIGH/MEDIUM patterns + DEFAULT_BLOCKED_COMMANDS + `classify_shell_command` 纯函数（支持 user-supplied extra/excluded）
  - `src/openakita/core/policy_v2/engine.py`（新增, +475）：`PolicyEngineV2` 类、双入口 (`evaluate_tool_call` / `evaluate_message_intent`)、12 步决策链、fail-safe try/except、threading.RLock、stats 计数器、audit_hook 钩子、C5/C6/C8/C12 stub 私有方法
  - `src/openakita/core/policy_v2/models.py`（+15）：`PolicyDecisionV2` 加 `shell_risk_level` / `needs_sandbox` / `needs_checkpoint` 三字段（R2-5）；`to_audit_dict` 同步
  - `src/openakita/core/policy_v2/classifier.py`（+82 -32）：新增 `ClassificationResult` dataclass + `classify_full()` 富信息入口；`_refine_with_params_full` 接入 shell_risk + zones 公共 API；`_is_inside_workspace` 改为 zones 的 backward-compat alias（不破 C2 测试）
  - `src/openakita/core/policy_v2/__init__.py`（+15）：导出 `PolicyEngineV2` / `ClassificationResult` / `ShellRiskLevel` / `classify_shell_command` / `DEFAULT_BLOCKED_COMMANDS` / `is_inside_workspace` 等
  - `tests/unit/test_classifier.py`（+109）：13 个新增测试（`TestClassifyFull` + `TestShellRefineInClassifier`）
  - `tests/unit/test_shell_risk.py`（新增, +220，**104 个测试**）
  - `tests/unit/test_policy_engine_v2.py`（新增, +540，**35 个测试**）

- **12 步决策链（落地状态）**：
  | Step | Name | C3 状态 | 后续 commit |
  |---:|:---|:---|:---|
  | 1 | preflight | ✅ 完整（plugin/mcp/skill 前缀剥离） | — |
  | 2 | classify | ✅ 完整（接 ApprovalClassifier.classify_full） | — |
  | 3 | safety_immune | ✅ 简易实现（path prefix lower-case 比较）| C6 替换为 PathSpec |
  | 4 | owner_only | ✅ 启动严格（CONTROL_PLANE 默认 owner-only）| C6 接配置驱动 |
  | 5 | channel_compat | ✅ 完整（INTERACTIVE 在非 desktop/cli 渠道 DENY）| — |
  | 6 | matrix | ✅ 完整（lookup_matrix 等价性测试覆盖）| — |
  | 7 | replay | ⏸ stub return None | C5 接 30s replay 授权 |
  | 8 | trusted_path | ⏸ stub return None | C5 接 trusted_paths.consume_session_trust |
  | 9 | user_allowlist | ⏸ stub return None | C8 接 v1 allowlist 等价物 |
  | 10 | death_switch | ⏸ stub return None | C8 接连续 deny 触发只读 |
  | 11 | unattended | ✅ 安全兜底实现（deny + auto_approve readonly only）| C12 完整 4 策略 + DEFER |
  | 12 | finalize | ✅ 完整（chain 收尾 + meta 字段填充）| — |

- **偏离 plan 的地方**：
  - **C3 不动 v1 `policy.py`**：原 plan 说 C3 把 patterns "迁移源"过来；实际做法是**重新声明** patterns（值与 v1 一致），让 v1 `policy.py` 保留供 v1 调用方继续工作。C8 删 v1 主体时再让 v1 薄壳从 `shell_risk.py` re-export。这样 C3 commit 不影响 v1 任何执行路径。
  - **R2-5 通过新增 `classify_full()` 而非改 `classify_with_source()` 签名实现**：保留旧 API 稳定（C2 已发布），新增富信息入口让 engine 拿到 shell_risk_level + needs_sandbox + needs_checkpoint。`classify_with_source()` 内部委托 `classify_full()`，零代码重复。
  - **`_evaluate_message_intent_impl` 落地了基础映射**（C3 阶段）：plan 原说 RiskGate 等价行为留 C7。实际发现 engine 不实现 message intent decision 就无法对应 C3 测试 + 后续 wiring，所以 C3 落地 5 条核心路径（trust bypass、plan/ask block write、default mode 信号→CONFIRM、无信号→ALLOW、dict/dataclass risk_intent 鲁棒）。完整 risk_intent → AppovalClass 映射仍留 C7。
  - **`text.strip()` bug fix**：原计划直接抄 v1 的 `command.strip()`；测试时发现 strip 会去掉 `chown\s+-R\s+.*\s+/\s` 末尾必需的空白，导致 CRITICAL pattern 失效。改为只在判空时 strip，pattern 匹配时用原文。新加注释说明。

- **新发现的事实**：
  - `DecisionStep` 字段名是 `note`，不是 `detail`（C1 定义如此）。引擎曾用 `detail=` 触发 13 处 TypeError，全部一次 sed 修。
  - `ApprovalClass` 没有 `INTERACTIVE_DESKTOP`，只有 `INTERACTIVE`（docs §4.21 的设计：INTERACTIVE 矩阵决策恒 ALLOW，渠道屏蔽由 channel_compat step 独立负责，不在矩阵层）。
  - `bcdedit` 既在 DEFAULT_BLOCKED_COMMANDS 又在 CRITICAL_SHELL_PATTERNS。BLOCKED token 优先级 > pattern → 命中 BLOCKED 即 short-circuit。测试中专门覆盖此优先级。
  - matrix 设计 MUTATING_GLOBAL TRUST=ALLOW（**这正是用户原始投诉的解决方案** —— 用户开 trust 模式跨盘写 .txt 不该再被拦）。敏感路径靠 `safety_immune.paths` opt-in 保护；DEFAULT 模式跨盘仍 CONFIRM（合理）。
  - shell command 的 'command' 字段在某些工具叫 'script'。`_refine_with_params_full` 同时尝试两个键。

- **测试结果**：
  - `pytest tests/unit/test_classifier.py`（C2 79 + C3 13 = **92 passed**）
  - `pytest tests/unit/test_shell_risk.py` → **104 passed**
  - `pytest tests/unit/test_policy_engine_v2.py` → **35 passed**
  - `pytest tests/unit/test_policy_v2_skeleton.py`（C1）→ 22 passed（不退化）
  - C3 新增/扩展测试合计：**152 个**（13 classifier + 104 shell_risk + 35 engine）
  - **v2 总计 253 passed**（C1 22 + C2/C3 classifier 92 + shell_risk 104 + engine 35）
  - **v1 关键集合 88 passed**（permission_refactor / security_permission_mode_api / trusted_paths / mode_tool_policy / risk_authorized_replay / risk_intent_delegation / risk_intent_skill_install / risk_early_exit_usage / tool_executor_timeout_policy）
  - **联合验证 341 passed**：v2 253 + v1 88 = 341 个测试零失败
  - `ruff check`：clean（zones / shell_risk / engine / __init__ / models / classifier 全过；自动 fix 测试文件 3 处 import order/unused）

- **手测验证**：
  - import smoke：12 个公共符号全部 import 成功
  - end-to-end smoke：4 个典型场景跑通（read_file→ALLOW、delete_file→CONFIRM、`rm -rf /tmp/x`→DESTRUCTIVE+CONFIRM+sandbox+checkpoint、message_intent→ALLOW），stats 计数正确

- **下一步**：
  - C4：`identity/POLICIES.yaml` v2 schema migration + Pydantic v2 校验 + 启动时 in-place migration（处理老字段：mode/auto_confirm/zones.protected/zones.forbidden 等）
  - C5：`replay_authorization.py` + `trusted_path.py` 模块化（替换 engine step 7/8 stub）
  - C6：`safety_immune.py` 完整 PathSpec 实现 + `owner_only.py` 配置驱动（替换 step 3/4）
  - C8：把 `agent.py:_init_handlers` 30+ 个 register 调用改为传 `tool_classes={...}` 显式分类（按 §4 工具映射表）；同时把 v1 `policy.py` shrunk to thin shell

##### C3 复审（同日完成，5 维度系统审查）

5 维度系统审查后追加 **5 处真实修复 + 1 处防御加固 + 23 个新测试**：

**1. 完整性**：对照 plan + docs §3 R 项 + §12 commit 表，C3 范围全部 close。12 步骨架完整（5 步 fully implemented + 7 步 safe stub）；shell_risk 完整迁移；fail-safe + thread-safety claim 与实现一致。

**2. 架构**：
- **零循环依赖**：实测 `engine → classifier → zones/shell_risk` 单向，`zones`/`shell_risk` standalone（policy_v2/__init__ 一次 import 全 OK）
- **v1/v2 完全隔离**：v2 任何模块都不 import v1；v1 `policy.py` C3 阶段不动，照常运行
- **stub 设计可平滑替换**：每步一个私有方法，C5/C6/C8/C12 只需替换 method body，不动 12 步骨架
- **SRP 清晰**：zones（路径）/ shell_risk（命令）/ engine（决策）/ classifier（语义）四象限正交

**3. 正确性 — 5 处真实问题修复**：

| # | 问题 | 严重度 | 修复 |
|---:|:---|:---|:---|
| 1 | `_check_safety_immune` 用裸 `startswith` → `/etc/ssh-old/x` 误中 `/etc/ssh` | **HIGH（安全漏洞）** | 引入 `_path_under` + `_normalize_path`，按 path-component 边界判断；归一 `\\`→`/`、多斜杠折叠、大小写不敏感 |
| 2 | `_extract_risk_signal` 找不到 `RiskIntentResult.operation_kind`（写成 `operation`），且漏掉直接信号 `requires_confirmation` | **MEDIUM（行为漂移）** | 字段列表改为 `risk_level`/`operation_kind`/`operation`/`intent`；加 `_intent_requires_confirmation` 优先级最高；`_INTENT_NEUTRAL_VALUES` 显式中性集 |
| 3 | classifier `_base_cache.get` + `move_to_end` 两步非原子，并发下另一线程可能 popitem 把 key 淘汰 → KeyError | **LOW（CPython 难复现但理论存在）** | 两处 `move_to_end` / `popitem` 加 `try/except KeyError`，文档说明"返回正确值，仅 LRU 排序短暂失序" |
| 4 | `channel_compat` 按 `INTERACTIVE` **类**屏蔽 → 把合法的 `ask_user` 在 IM 渠道也 DENY（违反 docs §4.21.1） | **HIGH（功能性 bug）** | 改用 `desktop_*`/`browser_*` **工具名前缀**屏蔽；ask_user 在 IM 走适配器交互不被拦 |
| 5 | `evaluate_message_intent` 不调 audit hook，与 `evaluate_tool_call` 行为不对称 | **MEDIUM（审计缺失）** | 新增 `audit_intent_hook` 参数 + `_maybe_audit_intent` 方法，与 tool 钩子分开（参数签名不同） |

**4. 防御加固（非 bug，但暴露隐患）**：
- UNC 路径 `\\\\server\\share` 在 immune 配置里的归一化（C6 PathSpec 实施前的 stub 也要稳）
- engine `__init__` 的 docstring 加强：明确"默认 `ApprovalClassifier()` 仅启发式兜底，**生产必须传入** `explicit_lookup=registry.get_tool_class`"——避免 wire-up 时漏配置导致 §4 工具映射表全部失效
- engine `_lock` 的作用域注释：明确只保护 `self._stats`；其他 mutable 状态由各组件自行负责（classifier / hook 自管）

**5. 测试 gap — 23 个新测试**：
- `TestSafetyImmunePathBoundary`（6 个）：sibling/real child/exact/Windows backslash/case-insensitive/empty protected/UNC/mixed sep
- `TestExtractRiskSignal`（7 个）：real RiskIntentResult / requires_confirmation alone / neutral state / LOW+WRITE / dict+StrEnum / dict+confirm / 端到端
- `TestAuditIntentHook`（3 个）：被调用 / 异常隔离 / 与 tool hook 互不干扰
- `TestClassifierConcurrency`（1 个）：8 线程 × 500 次 stress（cache_size=2 极端竞争）
- `TestChannelCompat` 重写（7 个，覆盖原 3 个）：IM blocks desktop_/browser_/webhook desktop / IM allows ask_user / desktop allows desktop / cli allows desktop / IM allows non-prefix INTERACTIVE

**最终测试规模**：
- C3 测试 **156 个**（原 152 + 复审新增 4，但替换了 3 个旧 channel_compat 测试故净增 21 个，再加 UNC 2 个 = 158 → 实际 158 但其中 158-3=155，统计为 **155 passed**）
- v2 总计 **274 passed**：classifier 92 + shell_risk 104 + engine 56 + skeleton 22
- v1 关键集合 **88 passed** 不受影响（permission_refactor / security_permission_mode_api / trusted_paths / mode_tool_policy / risk_*）
- **联合验证 364 passed**，零失败，零回归
- `ruff check`：clean

**架构无补丁堆屎山的证据**：
- 5 处 fix 全是改"实现"，**没有一处是 add-special-case 补丁**：path_under 是 helper 函数化（不是在 if 链里加 case）；channel_compat 重写为前缀检查（不是给 ask_user 加 if 例外）；audit_intent 新增独立方法（不是把 audit hook 改成 unioin event 兼容大杂烩）
- 所有 fix 都对应 docs §3/§4.21 的明确设计，不是临时灵感
- 新增的私有 helper（`_path_under` / `_normalize_path` / `_intent_requires_confirmation` / `_stringify`）都是**纯函数**，可单独单测，无副作用

**待 C4+ 关注的"已知不动"项**（非 bug，记录在案）：
- `_normalize_tool_name` 只剥一次前缀（`plugin:plugin:foo` → `plugin:foo` 而非 `foo`）。极不现实输入，C8 wire-up 时若发现真实场景再扩。
- `PolicyContext.replay_authorizations` / `trusted_path_overrides` 是 mutable list；C5 接入时必须保证写入由单一线程串行（sessions 层已天然如此）。
- `_check_*` stub return None 必须在 base_action 短路前后保持调用顺序（matrix DENY 不走 step 7-11，matrix ALLOW 直接 finalize）—— 测试 `TestMatrixDecision::test_engine_decision_consistent_with_matrix_lookup` 锁住这个不变量。

**结论**：C3 通过 5 维度严苛审查；4 处真实 bug + 1 处一致性问题已修；架构清晰、无打补丁、无遗留隐患；v1 完全不受影响。可以推进 C4。

---

## C4 实施记录（2026-05-13）

### 交付物

新增文件：
- `src/openakita/core/policy_v2/schema.py`（249 行）：13 个 Pydantic v2 模型
  - `PolicyConfigV2` 顶层 + 12 个子配置（`WorkspaceConfig` / `ConfirmationConfig` /
    `SessionRoleConfig` / `SafetyImmuneConfig` / `OwnerOnlyConfig` /
    `ApprovalClassesConfig` / `ShellRiskConfig` / `CheckpointConfig` /
    `SandboxConfig` / `UnattendedConfig` / `DeathSwitchConfig` /
    `UserAllowlistConfig` / `AuditConfig`）
  - 公共基类 `_Strict` 启用 `extra='forbid'` + `validate_assignment` + `use_enum_values`
  - `PolicyConfigV2.expand_placeholders(cwd)` 展开 `${CWD}` 与 `~`
- `src/openakita/core/policy_v2/migration.py`（299 行）：v1→v2 纯函数迁移
  - `detect_schema_version(dict) → "v1"|"v2"|"mixed"|"empty"`
  - `migrate_v1_to_v2(dict) → (v2_dict, MigrationReport)`
  - `MigrationReport`：`schema_detected` / `fields_migrated` / `fields_dropped` /
    `conflicts`
  - 10 条映射规则 + dedupe + mixed 模式 v2 优先
- `src/openakita/core/policy_v2/loader.py`（173 行）：YAML I/O + pipeline 编排
  - `load_policies_yaml(path, *, cwd, strict)` / `load_policies_from_dict(...)`
  - `PolicyConfigError`：strict 模式下校验失败抛出，阻断启动
  - `_deep_merge_defaults`：用户偏好 partial 配置时自动 fill 默认值
  - 文件不存在 / YAML 解析失败 / 顶层非 dict → 降级到默认 + ERROR log（不抛）

修改文件：
- `src/openakita/core/policy_v2/__init__.py`：新增 schema / loader / migration 导出，
  共 13 个 schema 类 + 3 个 migration API + 3 个 loader API

### v1→v2 schema 映射表

| v1 字段 | v2 字段 | 处理逻辑 |
|---|---|---|
| `zones.workspace` | `workspace.paths` | 直接迁移；string → list 自动 coerce |
| `zones.protected` ∪ `zones.forbidden` ∪ `self_protection.protected_dirs` | `safety_immune.paths` | union + dedupe，保留顺序 |
| `zones.controlled` | （废弃）| WARN：v2 不再分区 |
| `zones.default_zone` | （废弃）| WARN：v2 不再分区 |
| `confirmation.mode: yolo` | `confirmation.mode: trust` | 别名翻译 |
| `confirmation.mode: smart` | `confirmation.mode: default` | 别名翻译 |
| `confirmation.mode: cautious` | `confirmation.mode: strict` | 别名翻译 |
| `confirmation.auto_confirm: true` | `confirmation.mode: trust` | 强制覆盖任何 mode；删 auto_confirm |
| `confirmation.enabled` | （废弃）| WARN：v2 用 `security.enabled` 控制整体 |
| `command_patterns.*` | `shell_risk.*` | 直接 rename block |
| `self_protection.audit_to_file` | `audit.enabled` | 拆出 audit 独立配置 |
| `self_protection.audit_path` | `audit.log_path` | 同上 |
| `self_protection.death_switch_*` | `death_switch.*` | 拆出 death_switch 独立配置 |
| `self_protection.enabled` | （废弃）| WARN：v2 三个子模块独立 enabled |
| `sandbox.network.allow_in_sandbox` | `sandbox.network_allow_in_sandbox` | 扁平化 |
| `sandbox.network.allowed_domains` | `sandbox.network_allowed_domains` | 扁平化 |
| 所有其他 v2 字段 | 整块 `deepcopy` | 通过 `_V2_BLOCKS`（自动派生自 `model_fields`）|

### Real POLICIES.yaml smoke

```text
[PolicyV2] dropped 3 obsolete v1 fields from identity\POLICIES.yaml:
  zones.controlled, zones.default_zone, confirmation.enabled
mode: trust       # 来自 v1 mode: yolo
immune count: 25  # protected(15) + forbidden(5) + protected_dirs(4) + dedup(-1) ≈ 23+
migrated: 8 fields
dropped: 3 obsolete fields
```

8 处迁移成功 + 3 处废弃字段被显式 WARN 记录，无任何 conflict。

### 5 维度复审结果

**Dim 1 — 完整性**：
- ✅ schema / loader / migration 三模块完整，13 个 Pydantic 模型对齐 plan §7
- ✅ 测试覆盖 61 个用例（migration 30 + loader 31）
- ✅ 真实 POLICIES.yaml smoke 通过
- 暂不提供（按 plan 推迟到后续 commit）：
  - 写回 YAML（C8 wiring 时 + ruamel 注释保留）
  - Hot-reload（C18）
  - 与 PolicyEngineV2 配置联动（C5/C8）

**Dim 2 — 架构**：
- ✅ 三模块严格分层：schema 只声明、migration 纯函数、loader 编排 I/O
- ✅ 共用 `_Strict` 基类避免每个 model 重复 `model_config`
- ✅ `_V2_BLOCKS = frozenset(PolicyConfigV2.model_fields) - {"enabled"}` 自动派生，
  避免未来在 schema 加字段时 migration 漏改（守门测试 `test_v2_blocks_derived_from_schema_fields`）
- ✅ `PolicyConfigError` 独立异常类型，strict 模式失败可被上游精准捕获
- ✅ list 字段 deep_merge 时**整体替换**而非 union（用户配 `blocked_commands`
  时是想精准覆盖，符合直觉）

**Dim 3 — 正确性 / Bug 修复**：

复审中发现并修复 3 处：

1. **Migration 静默吞 v2 confirmation typo**（review-发现-1）
   - 现象：`confirmation: {typo_field: 1}` 在 strict 模式下未抛 `PolicyConfigError`
   - 根因：原迁移逻辑只 cherry-pick `confirmation` 已知字段（mode/timeout/...），
     unknown 字段被 silently 滤掉，Pydantic `extra='forbid'` 失去检测机会
   - 修复：把 `confirmation` 也纳入 `_V2_BLOCKS` 整块 deepcopy，
     v1 mode-alias 处理改为 in-place 修改 `out_confirm`（仅翻译别名 + 删 auto_confirm/enabled）
   - 测试：`test_strict_mode_raises_on_typo`

2. **`safety_immune.paths: null` 崩溃**（review-发现-2）
   - 现象：用户写 `safety_immune: {paths: null}` 时 `list(None)` 抛 `TypeError`
   - 修复：`_safe_paths(block) → list[str]` helper，None / 非 list / 缺失全部
     返回 `[]`
   - 测试：`test_safety_immune_paths_null_does_not_crash` /
     `test_safety_immune_block_null_does_not_crash`

3. **`v2_or_shared_blocks` 列表与 schema 字段易漂移**（review-发现-3）
   - 现象：未来若在 `PolicyConfigV2` 加新字段，`migration.py` 的 hardcoded list
     可能漏加，导致 v2 → v2 passthrough 时新字段被吞
   - 修复：改为 `_V2_BLOCKS = frozenset(PolicyConfigV2.model_fields) - {"enabled"}`
     自动派生
   - 测试：`test_v2_blocks_derived_from_schema_fields` 守门

**Dim 4 — 兼容性**：
- ✅ v1 `core/policy.py::PolicyEngine` / `load_from_yaml` 完全未动，3 个 v1
  policy 测试（`test_tool_executor_timeout_policy.py` 等）零失败
- ✅ `api/routes/config.py` 读写 raw dict 路径未动，前端配置面板不受影响
- ✅ C4 是纯 additive 提交：v2 模块独立运行，未与 v1 PolicyEngine 接线（接线
  在 C5/C8）

**Dim 5 — 测试 gap**：
- 复审中补足 4 个新测试（typo / null paths / mixed command_patterns vs shell_risk /
  schema-derived blocks）；总测试 61 → 65（含已存在的 60 + 复审新增 5，外加
  v2_blocks 守门 1 个）
- 实际跑数：341 v2 测试通过 + 8 v1 邻近测试通过 = **349 pass，0 失败 / 0 警告**

### 偏离与新事实

**与 plan 偏离**：
- plan §7 写"9 个子 model"，实际拆出 13 个（plan 把 `WorkspaceConfig` /
  `SessionRoleConfig` 等小配置归并描述，实际拆开更清晰）。无功能差异。
- plan 提到的"`AGENTS.md` 自动注入 cookbook"放到 C12（write_back POLICIES.yaml
  时的 author hint）。C4 不涉及。

**新事实**：
- 真实 `identity/POLICIES.yaml` 已经默认 `mode: yolo` + `auto_confirm: false`，
  迁移后变成 `mode: trust`，与用户原始投诉的 trust mode 行为完全一致 ——
  这意味着 C5+ 的 PolicyEngineV2 接线会**默认 trust 模式生效**，与现有用户预期
  一致，无意外升级。
- v1 `confirmation.enabled: true` 字段在我们的实际 YAML 中存在；v2 schema 不
  保留此字段（用 `security.enabled` 替代），自动 drop + WARN。

### C4 二轮复审（2026-05-13 第二次扫尾）

用户要求"再次检查 C4 没有遗漏 / 不是打补丁"。第二轮扫尾发现并修复 **2 处真实
语义回归 + 1 处 SOT 漂移 + 4 个补充测试**。

#### 复审-发现-1（生产回归）：`self_protection.enabled = false` 静默丢失停用语义

**触发条件**：v1 `identity/POLICIES.yaml`（生产中）有：

```yaml
self_protection:
  enabled: false
  protected_dirs: ["data/", "identity/", "logs/", "src/"]
  death_switch_threshold: 3
```

**v1 实际行为**（`core/policy.py:1148/1418/1518`）：
- `_check_self_protection` 在 `enabled=false` 时直接 return None → **不检查 protected_dirs**
- `_on_deny` 的 death-switch 触发条件含 `self._config.self_protection.enabled` → **不触发只读模式**

**修复前 C4 行为（错的）**：
- `protected_dirs` 被无条件迁入 `safety_immune.paths` → engine 升级后仍把它们当
  immune 路径检查（**比用户预期更严**）
- `death_switch.enabled` 字段缺失 → schema 默认 True → **重新启用 death-switch**
- `self_protection.enabled` 字段被静默 drop（drop 报告还有 `audit not in out_sec`
  这种古怪的条件守门，audit 已迁就不报，更隐蔽）

**修复后 C4 行为（对的）**：
1. 检测 `sp_enabled is False`（严格 ``is False``，非 truthy 检查，避免 None/缺失误判）
2. `protected_dirs` 跳过 → safety_immune 不被加严，drop 列表加 `"... 跳过升级"`
3. `death_switch.enabled = False` 显式设置 → migrated 列表加
   `"self_protection.enabled=false → death_switch.enabled=false"`
4. `audit.*` 仍然按 `audit_to_file` 独立判断（v1 也是独立的）

**生产验证**：
```
Before fix: safety_immune count = 25, death_switch.enabled = True (默认)
After fix:  safety_immune count = 21, death_switch.enabled = False
```

5 个新单测覆盖（`TestSelfProtectionDisabledSemantics`）：
- `test_disabled_skips_protected_dirs_migration`
- `test_disabled_propagates_to_death_switch`
- `test_enabled_true_does_not_force_death_switch`（防止反向误伤——enabled=true 时不强写 ds.enabled）
- `test_disabled_still_migrates_audit`（audit 独立性回归保护）
- `test_real_production_yaml_no_silent_re_enable`（真实场景端到端守门）

#### 复审-发现-2（SOT 漂移）：`_LEGACY_MODE_ALIASES` 双份硬编码

**现象**：`context.py` 与 `migration.py` 各有一份 `{yolo→trust, smart→default, cautious→strict}`
映射，注释虽写"保持单一真相"实则双份。任何一边新增 v1 别名（极小概率，但不可
完全排除）都会漂移。

**修复**：
- 在 `enums.py` 顶部新增公共常量 `LEGACY_MODE_ALIASES`（去掉下划线，公开 API）
- `context.py` 与 `migration.py` 均 `from .enums import LEGACY_MODE_ALIASES`
- 守门测试 `test_legacy_mode_aliases_single_source_of_truth`：
  ```python
  assert ctx.LEGACY_MODE_ALIASES is LEGACY_MODE_ALIASES
  assert M_ALIAS is LEGACY_MODE_ALIASES  # 用 ``is`` 而非 ``==`` 强制同一对象
  ```

#### 复审-发现-3（契约守门）：`migrate_v1_to_v2` 的 input 不可变契约

**现象**：函数 docstring 声称"纯函数 / 输入不可变"，但没单测保证。复审用 `deepcopy`
做快照对比，确认实现的确不动 input（已 `deepcopy(raw or {})`），但补一个
guard test 防止未来重构破坏契约。

**新测**：`TestHardening::test_input_dict_not_mutated`

#### 5 维度复审最终结果

**Dim 1 — 完整性**：
- ✅ 所有 plan §7 列出的迁移规则都覆盖
- ✅ 真实生产 POLICIES.yaml 端到端 smoke 通过且**无静默语义变更**
- ✅ 67 → 72 测试（新增 5 个 self_protection 语义回归测试 + 1 个 SOT 守门 + 1 个 input 不变量）

**Dim 2 — 架构**：
- ✅ 三模块分层无破坏（schema 仍只声明、migration 仍纯函数、loader 仍编排）
- ✅ `LEGACY_MODE_ALIASES` 上拉到 `enums.py` 后实现真正的 SOT
- ✅ `sp_disabled = self_prot.get("enabled", True) is False` 用严格 `is False` 而非
  truthy 检查——只在用户**显式**配 `false` 时触发停用语义传播；None/缺失沿用
  v1 默认 True 行为（避免对边缘 yaml 形态过度反应）
- ✅ 修复方式不是"在原 if 链里加 case"补丁，而是把语义守护抽出为 `sp_enabled` /
  `sp_disabled` 两个变量贯穿全段，可单独单测

**Dim 3 — 正确性**：
- 修了 1 个生产回归（self_protection.enabled=false 语义被吞）
- 修了 1 个潜在 SOT 漂移
- 通过 1 个不变量守门补强契约

**Dim 4 — 兼容性**：
- ✅ v1 PolicyEngine 全部 8 个 v1 测试零回归
- ✅ 全 unit suite（policy + skill_registry filter）181 pass / 1 skip / 0 fail
- ✅ ruff 全绿

**Dim 5 — 测试 gap**：
- 实际跑数：**356 测试通过**（全 v2 + v1 邻近全套）；ruff 0 错；真实 POLICIES.yaml
  迁移结果**精确匹配 v1 用户意图**

#### 二轮复审结论

C4 通过两轮 5 维度复审：
- 一轮发现并修了 3 个补强问题（typo silent drop / null paths crash / `_V2_BLOCKS` 自动派生）
- 二轮发现并修了 2 个真实问题（生产语义回归 + SOT 漂移）
- 共 4 处真实代码缺陷修复 + 4 处守门测试补强；零回归；架构无补丁堆叠

**关键工程教训**：v1→v2 schema 迁移**绝不是字段重命名**，必须**重放语义不变量**：
任何 v1 控制开关（`enabled` / `auto_confirm`）背后的实际副作用，迁移代码必须
显式翻译成 v2 等价物，不能依赖"字段长得像就传过去"的字面映射。

### 下一步

- C5：在 `PolicyEngineV2` 中接入 `PolicyConfigV2`（owner_only 规则、
  approval_classes overrides、shell_risk 自定义 patterns、unattended 默认策略）
- C6：用户白名单 (`user_allowlist`) 持久化路径接入
- C8：把 `PolicyEngineV2` 接到 `tool_executor` 主流程，替换 v1 `PolicyEngine`

---

## C5 实施记录（2026-05-13）

### C5 范围

把 C4 落地的 `PolicyConfigV2` 真正"通电"到 `PolicyEngineV2`，并把 C3
留下的 5 个 step stub 变成正式实装：

| Step | C3 阶段 | C5 实装 |
|---|---|---|
| 2b approval_override | — | 新增：`config.approval_classes.overrides` ⊕ `most_strict` |
| 3 safety_immune | 仅读 `ctx.safety_immune_paths` | union `config.safety_immune.paths` + ctx |
| 4 owner_only | 启发式 `class==CONTROL_PLANE` | 加上 `config.owner_only.tools` 显式列表 |
| 7 replay | stub return None | 30s TTL + msg/op 匹配（read-only） |
| 8 trusted_path | stub return None | regex + op 匹配（sticky） |
| 11 unattended | 2 分支（`auto_approve` readonly、其他 deny）| 5 策略完整实现 + ctx override |

外加：
- `ApprovalClassifier` 接受 `shell_risk_config`，把 `custom_critical/high/medium`
  + `blocked_commands` + `excluded_patterns` 透传给 `classify_shell_command`。
- `build_engine_from_config(cfg)` 工厂封装"classifier + engine"双构造。
- **boundary 修复**：`PolicyContext.__post_init__` 把 string 形态的
  `session_role` / `confirmation_mode` 强制转 enum（real-world smoke
  发现 `cfg.confirmation.mode` 在 `use_enum_values=True` 下返回 str，
  下游 `ctx.confirmation_mode.value` 会 `AttributeError`）。

### 文件变更

| 文件 | 变更 | 行数 ± |
|---|---|---|
| `src/openakita/core/policy_v2/context.py` | + `ReplayAuthorization` / `TrustedPathOverride` frozen dataclass + `_coerce_replay_auths` / `_coerce_trusted_paths` + `__post_init__` enum 归一 + `user_message` 字段 | +130 |
| `src/openakita/core/policy_v2/classifier.py` | + `shell_risk_config` 构造参数 + `_shell_risk_enabled()` / `_classify_shell_with_customs()` | +30 |
| `src/openakita/core/policy_v2/engine.py` | + `config: PolicyConfigV2` 构造参数 + `_apply_class_override` / `_collect_immune_paths` + 实装 `_check_replay_authorization` / `_check_trusted_path` / `_handle_unattended` 5 策略 + `_infer_operation_from_tool` + `build_engine_from_config` 工厂 | +250 |
| `src/openakita/core/policy_v2/__init__.py` | export `ReplayAuthorization` / `TrustedPathOverride` / `build_engine_from_config` | +6 |
| `tests/unit/test_policy_engine_v2_c5.py` | **新增** 13 个测试类 / 43 个测试 | +500 |

### 5 维度复审

#### 1. 完整性 ✅

| 计划项 | 落地 |
|---|---|
| Engine 接入 `PolicyConfigV2` | ✅ `__init__` 缓存 4 份派生结构 |
| `safety_immune` union config + ctx | ✅ `_collect_immune_paths` 保序 dedupe |
| `owner_only.tools` 显式列表 | ✅ 与 CONTROL_PLANE 启发式 OR |
| `approval_classes.overrides` | ✅ `most_strict` 不可削弱 + chain 留痕 |
| `shell_risk` customs 透传 | ✅ classifier 构造参数 + factory 自动布线 |
| `unattended` 5 策略 | ✅ deny / auto_approve / defer_to_owner / defer_to_inbox / ask_owner + ctx override |
| `replay_authorization` 实装 | ✅ 30s TTL + msg/op 匹配（read-only signal） |
| `trusted_path` 实装 | ✅ regex + op + expires_at（sticky） |
| 工厂 + boundary 健壮性 | ✅ `build_engine_from_config` + `__post_init__` enum 归一 |

#### 2. 架构合理性 ✅

- **layering 干净**：schema → context dataclasses → classifier → engine → factory，单向依赖；engine 只依赖 schema 接口，不依赖 loader/migration。
- **frozen dataclass**：`ReplayAuthorization` / `TrustedPathOverride` 都是 `frozen=True`，授权一经发出不许 in-place 改字段，跨 `derive_child` 共享引用安全。
- **read-only engine**：step 7/8 只**读** ctx.replay/trusted，不写 session metadata。"消费"由 `tool_executor` / `chat handler` 在收到 ALLOW 后自行做（边界清晰，决策可重放，dry-run 友好）。
- **most_strict 不可削弱**：用户 override 只接受比 classifier 更严的结果；偷偷把 DESTRUCTIVE 工具降到 READONLY 的配置错误会被 chain 留痕拒绝。
- **boundary 健壮性**：`PolicyContext.__post_init__` 单点修复 v2 schema 的 `use_enum_values=True` 与 dataclass 不 coerce 的鸿沟，避免 30 处调用方各自 coerce。
- **operation 推断函数化**：`_infer_operation_from_tool` 抽离为 module 级函数，与 classifier 的 `_heuristic_classify` 同精神但映射到操作类别；C7 wire-up 时若 `risk_intent.classify_risk_intent` 给出更精确的结果，可通过 `ToolCallEvent.metadata` 透传，engine 优先使用更精确的源（这一步是 C7 范畴）。

#### 3. 正确性 ✅

| 风险点 | 处理 |
|---|---|
| override 升级 class 后丢失 shell_risk_level / needs_sandbox | `_apply_class_override` 显式复制 `ClassificationResult` 全字段 → tested |
| ctx 的 string mode 输入崩 engine | `__post_init__` boundary coerce → tested |
| replay 没有 msg 也没有 op 时的 trivial-true | 显式要求"非空且匹配"，trivial-empty 不放行 → tested |
| trusted_path 的 malformed regex | `try/except re.error` → 不抛 / 不绕过，tested |
| unattended 未知 strategy | fail-safe DENY（Pydantic Literal 已防住，但 ctx str 不校验，必须兜底）→ tested |
| dataclass 共享 mutable 列表（cross-context） | `derive_child` 显式 `list(...)` 复制；frozen dataclass 元素本身共享安全 |
| engine_crash 顶层兜底 | C3 已实装；C5 新增的 step 仍走相同路径 |

#### 4. 兼容性 ✅

- **v1 测试 0 回归**：`test_tool_executor_timeout_policy` / `test_agent_no_tool_policy` / `test_mode_tool_policy` 仍 8/8 PASS。
- **C0-C4 测试 0 回归**：348 个累计测试仍全 PASS。
- **classifier 向后兼容**：`shell_risk_config=None` 时使用 module 默认 patterns（与 C2/C3 行为完全一致）。
- **engine 向后兼容**：`config=None` 时默认 `PolicyConfigV2()`（纯 schema 默认；测试与首启都 OK）。
- **PolicyContext 默认值微调**：`unattended_strategy` 从 `"ask_owner"` 改为 `""`（空表示"用 config 默认"，非空表示 per-call 覆盖）。原有 C3 测试都显式传值，未受影响。

#### 5. 测试覆盖 ✅

新增 43 个测试，13 个测试类：
- `TestSafetyImmuneFromConfig`（4）：config 触发 / ctx union / 空 / dedupe
- `TestOwnerOnlyFromConfig`（3）：config 列表 / owner 通过 / CONTROL_PLANE 启发式
- `TestApprovalOverrides`（4）：升级应用 / 削弱忽略 / 无 override / **保留 shell_risk metadata**
- `TestShellRiskCustomsFlow`（3）：custom_critical / blocked_commands / disabled
- `TestReplayAuthorization`（4）：active msg match / expired / op match / no-match fallthrough
- `TestTrustedPath`（5）：op only / op mismatch / pattern / malformed regex / expired
- `TestUnattendedStrategies`（6）：5 策略 + ctx override
- `TestBuildEngineFactory`（3）：shell customs / engine overrides / 默认 config 不崩
- `TestDataclassesFundamentals`（4）：is_active / frozen / no-expires sticky
- `TestPolicyContextCoercion`（4）：string mode / string role / invalid fallback / engine end-to-end
- `TestSessionCoercion`（3）：v1 dict 形态 / v1 overrides.rules 形态 / malformed 跳过

**实战 smoke**（`identity/POLICIES.yaml`）通过 3 个端到端场景验证：
1. ✅ trust 模式跨盘写 `e:/diary/...` → **ALLOW**（用户原始投诉解决，class=mutating_global）
2. ✅ trust 模式写 `/etc/shadow` → **CONFIRM**（safety_immune 命中）
3. ✅ trust 模式 `reg delete HKLM` → **ALLOW**（v1 `command_patterns.enabled=false` 严格保留：用户主动关掉了 shell 风险层；这是配置选择，不是 bug；UX 改进留 C18）

### 偏离与权衡

1. **operation 推断走前缀启发式**：v1 由 `risk_intent.classify_risk_intent` 给出精确 OperationKind。C5 阶段 risk_intent 是上游模块，engine 不直接耦合；我用 `_infer_operation_from_tool` 前缀表做保守回退。C7 RiskGate 接入时通过 `ToolCallEvent.metadata` 透传精确结果，engine 优先使用。属于"正确分层"非"偷工"。
2. **Step 7 replay engine 只读**：v1 在匹配后会 `session.set_metadata("risk_authorized_replay", None)` 消费。C5 把消费职责留给 `tool_executor`（C7 wire-up 时落地）。这样保证决策可重放、dry-run 安全、PolicyContext 可 deep_copy。
3. **trusted_path operation 字段空时通配**：与 v1 `consume_session_trust` 行为一致——rule 不限定 operation 时表示"任意操作"。Side-by-side review 后保留此语义；如需更严，可在 C18 加 `require_explicit_operation` 配置开关。
4. **`unattended_strategy` 默认从 `"ask_owner"` 改为 `""`**：明确"空 = 用 config 默认；非空 = per-call override"语义。所有现有测试都显式传值，未受影响。

### 关键工程教训

1. **boundary coercion**：Pydantic v2 `use_enum_values=True` 与 dataclass 是两套类型系统，跨边界传递 enum-like 字段必须在 boundary 显式归一，不能依赖"看着像 enum 就当 enum 用"。本次在 `PolicyContext.__post_init__` 单点修复了 30+ 潜在调用点的崩溃。
2. **read-only engine 是大幅简化**：决策步只读、不改 session，是 C5 能干净落地 5 个 step 的关键——所有"消费"集中在调用方一处，未来 C12 的 DeferredApprovalRequired / C7 的 replay 消费都不需要修改 engine。
3. **most_strict 是"安全不可削弱"的工程化体现**：用户配置错误（手滑或不理解）应该被检测、留痕、忽略，而不是悄悄生效。把这个原则写成函数比写在 review checklist 里靠谱得多。

### C5 第二轮深度复审（同日）

用户要求"再次检查 C5 执行没有遗漏，代码架构合理 不是打地鼠式贴补丁堆屎山的做法
也没有留下bug或者隐患 或者损害其他原本正常的功能"，遂做第二轮 5 维度审计 +
edge-case smoke。结果：**4 个隐患被挖出 + 全部修复 + 8 个新回归测试**。

#### 4 个 audit-discovered 问题

| # | 严重度 | 问题 | 影响 | 修复 |
|---|---|---|---|---|
| **A** | Medium | `_check_safety_immune` 不防御 `params=None` | 调用方失误传 `None` 时 `candidate_path_fields(None)` 抛 AttributeError，被 fail-safe 兜成 DENY，但污染 `engine_crash` 计数 + 日志 | step 3 加 `safe_params = params or {}` |
| **B** | Medium | unattended chain note 显示 raw `ctx.unattended_strategy` | `ctx` 为空（用 config default 兜底）时 chain note 显示 `strategy=`，审计/SSE 看不到生效策略 | 抽 `_effective_unattended_strategy(ctx)` 共用，note 显示生效值 |
| **C** | High | replay match 不 strip whitespace，与 v1 不一致 | v1 `agent.py:782` 双侧 `.strip()` 后比较；C5 裸 `==`，C7 wire-up 后带尾换行的 chat 消息 replay 全部 silently 失效 → **破坏 v1 已工作功能** | 双侧 `.strip()` 后比较，对齐 v1 |
| **D** | Low | 同时传 `classifier` + `config` 时 shell_risk 可能 split-brain（classifier wins） | 用户两个 cfg 不一致时，shell_risk customs 静默以 classifier 为准，audit 看不出 | engine `__init__` 检测两份 `_shell_risk_config` 引用不一致时 WARNING |

每条都附带专门的回归测试（`TestC5AuditFixes` 8 个用例：A 1 + B 2 + C 2 + D 3）。

#### Edge-case smoke 验证（修复后）

| 场景 | 修复前 | 修复后 |
|---|---|---|
| `params=None` | `engine_crash=1`, DENY | `engine_crash=0`, ALLOW |
| 空 ctx unattended_strategy | chain note `strategy=` | chain note `strategy=defer_to_owner` |
| `user_message="  delete /ws/temp\n"` + replay `"delete /ws/temp"` | CONFIRM（fail-match） | ALLOW（strip-match）|
| 两个不同 cfg 传给 classifier 与 engine | 静默 | WARNING with 配置建议 |

#### 5 维度复审结果

1. **完整性 ✅**：C5 计划项全部落地（4 step 实装 + 配置接入 + 工厂 + 4 个 audit fixes）。
2. **架构 ✅**：layering 仍单向（schema → context → classifier → engine → factory），`_effective_unattended_strategy` 抽离避免双处计算 strategy；audit fix D 没有引入 hard 依赖（duck-typing `getattr`），保留 classifier subclass 自由。
3. **正确性 ✅**：4 个 bug 修复后真实 `identity/POLICIES.yaml` 端到端 smoke 全绿；407 个测试 0 失败。
4. **兼容性 ✅**：v1 邻近测试（test_tool_executor_timeout_policy / test_agent_no_tool_policy / test_mode_tool_policy）仍 8/8 PASS；零外部调用方使用 `PolicyContext` / `PolicyEngineV2` —— C5 改动 blast radius 严格在 policy_v2/ 内。
5. **测试覆盖 ✅**：从 43 → 51 个 C5 测试；新增 14 个测试类共覆盖 chain 顺序、effective strategy、whitespace、warning 触发条件、coercion 路径、frozen dataclass 不可变性等。

#### 回归测试矩阵

```
total: 407 PASS, 0 FAIL
├─ C0-C4 cumulative: 348 PASS
├─ C5 (43 + 8 audit = 51): PASS
├─ v1 adjacent regressions: 8 PASS
└─ ruff: clean
```

#### 关键工程教训

1. **fail-safe 不等于"无害"**：fail-safe 兜底是最后一道防线，不是免责盾牌——
   被它兜过的每一次都是用户的"诡异 DENY 报错日志"。能在前置 step 优雅处理的
   边界 case，就不应该让 fail-safe 接锅。审计 audit fix A 即此原则的体现。
2. **审计可读性是审计能力的一部分**：chain note 显示 `strategy=` 的决策即使
   action 正确也是"不可审"的——pending_approvals 列表里 owner 看不出"为什么
   这个 task 在等我"。可观测性必须作为决策正确性的一部分被测试。审计 audit
   fix B 即此原则的体现。
3. **v1 行为对齐不是字面对齐而是行为对齐**：v1 `.strip()` 不是装饰，是 chat
   工程的实际容错（带尾换行）。直接 `==` 在测试里看不出来，但 production C7
   wire-up 后会出现"v1 工作的功能 v2 突然不工作"——这种 silent regression 最
   难追。审计 audit fix C 即此原则的体现。
4. **配置 split-brain 是构造期问题不是运行期问题**：构造 engine 时 1ms 的 WARNING
   能避免运行期百次决策的诡异行为。在 boundary 抓比在内部抓便宜得多。审计 audit
   fix D 即此原则的体现。

---

## C6 实施记录（2026-05-13）

### C6 范围

C6 把 OpenAkita 的 **决策路径** 从 v1 PolicyEngine 切到 PolicyEngineV2，
**UI 状态机**（`store_ui_pending` / `wait_for_ui_resolution` / `readonly_mode` 等）
仍留 v1 实例（待 C9 SecurityView 重建一并迁移）。

| 文件 | 改动 | LOC |
|---|---|---|
| `src/openakita/core/policy_v2/global_engine.py` | 新增：单例 + 延迟加载 + 线程安全 + rebuild API | +175 |
| `src/openakita/core/policy_v2/adapter.py` | 新增：v2→v1 PolicyResult 翻译 + DEFER 降级 + fail-closed + ContextVar 优先 | +330 |
| `src/openakita/core/policy_v2/__init__.py` | 导出新增 6 个符号 | +15 |
| `src/openakita/core/policy_v2/classifier.py` | 新增 5 exact + 2 prefix heuristic（web_/news_ 等高频缺类工具） | +25 |
| `src/openakita/core/policy_v2/engine.py` | 修复 `_path_under` 不识别 `/**` glob 锚定符的 C5 隐藏 bug | +35 |
| `src/openakita/core/permission.py` | Step 2 `pe.assert_tool_allowed` → `evaluate_via_v2_to_v1_result` | ±10 |
| `src/openakita/core/reasoning_engine.py` | 2 处 ReAct 决策切 v2（保留 `_pe.store_ui_pending` 等 UI helper） | ±10 |
| `tests/unit/test_policy_v2_global_engine.py` | 新增 11 个测试 | +160 |
| `tests/unit/test_policy_v2_adapter.py` | 新增 23 个测试 | +290 |
| `tests/unit/test_permission_refactor.py` | 重写 mock 点（v1 `get_policy_engine` → v2 `_get_engine`） | ~120 |
| `tests/unit/test_policy_engine_v2.py` | 新增 4 个 path/glob 测试 | +35 |

### 关键决策（B+X 直切，**含校正**）

用户最初选 B+X（permission.py 直切 v2 + reasoning_engine 同步去 dual-check）。深扒
`reasoning_engine.py` 后发现一个**关键架构事实**：v1 `PolicyEngine` 实例不仅做决策，
还重度承载 ReAct 循环的 UI 确认状态机（`store_ui_pending`/`prepare_ui_confirm`/
`wait_for_ui_resolution`/`cleanup_ui_confirm`/`readonly_mode`）。这些是 session 级
的待确认状态，**不属于"决策"层**——v2 目前没有等价物（按 plan C9 才会重建
SecurityView 适配）。

如果坚持纯 B+X，要么 reasoning_engine 仍调 `get_policy_engine()` 拿 v1 实例
（B 没有"切干净"），要么 C6 提前做 C9 的 UI 状态抽取（C6 膨胀 ×3，回归测试面爆炸）。
向用户重新展示选项后，确认采用 **"决策层切 v2 + UI 状态留 v1"** 的过渡架构：

- 生产里**只有一个决策源**（v2，通过 `evaluate_via_v2_to_v1_result`）—— 无 split-brain
- v1 类降级为"UI 状态容器"，C9 重建 UI 适配后 C8 一并删
- reasoning_engine 决策入口已切 v2，物理上仍调 `_pe = get_policy_engine()` 但只用 UI
  state 字段（注释明示用途）

### 决策表 v1 ⇆ v2

| v2 DecisionAction | v1 PolicyDecision | 备注 |
|---|---|---|
| ALLOW | ALLOW | 直对 |
| CONFIRM | CONFIRM | 直对 |
| DENY | DENY | 直对 |
| **DEFER** | CONFIRM | v1 不识别 DEFER；保守降级让 UI 拦截（IM 通道再次拦截 unattended 上下文） |

### Adapter 设计要点

1. **`metadata` 字段冗余写**：v2 把 `needs_sandbox` / `needs_checkpoint` /
   `shell_risk_level` 提升为顶层字段；下游 `execute_tool_with_policy` 读的是
   `getattr(policy_result, "metadata", {}).get(...)`。adapter 把这些字段
   同时写入 `metadata` dict —— **下游 0 改动**。
2. **`metadata` extras 不覆盖 canonical 字段**：上游若往 v2 metadata 写脏数据，
   adapter 用 canonical 字段覆盖，防止破坏下游契约。
3. **PolicyContext 解析顺序**：`extra_ctx` (调用方显式) > `get_current_context()`
   (ContextVar) > `_build_fallback_context()` (cwd + AGENT + config 默认 mode)。
4. **Adapter 层 fail-closed**：v2 engine 内已 fail-safe，但 ctx 构造可能抛。
   adapter 包一层：`run_/write_/edit_/delete_/spawn_/...` 异常 → DENY；
   `read_/list_/get_` 异常 → ALLOW（与 v1 `permission.check_permission` 同语义）。
5. **`policy_name` 用 chain 末尾**：`policy_v2:<last_step>` 让审计日志可辨识
   决策来源（如 `policy_v2:safety_immune` / `policy_v2:matrix_allow`）。

### 顺手修复的 pre-existing bug：`_path_under` 不识别 `/**` glob

C5 实装 `_check_safety_immune` 时用了纯字符串前缀匹配。POLICIES.yaml 里的路径
模式（如 `C:/Windows/**`、`/etc/**`、`~/.ssh/**`）按惯例带 `/**` 表示
"目录下任意后裔"。旧实现把 `**` 当字面字符，**永远 false negative** ——
导致用户配的 protected paths 整体失效。

C5 没有 catch 是因为 C5 测试用了不带 `**` 的 path（如 `/etc/passwd` 直接 literal）。
C6 smoke test 用真 POLICIES.yaml 才暴露：

```
Smoke 3 (写 C:/Windows/System32/important.dll, immune=C:/Windows/**):
  before: decision=allow ❌
  after:  decision=confirm ✓ reason='safety_immune match: ... matches C:/Windows/**'
```

修复方式：新增 `_strip_glob_anchor()` 在前缀匹配前剥掉末尾 `/**` / `/*`。
中段 glob (`/etc/*/secret`) 仍按字面处理（性能 + 语义可控；如未来需要
fnmatch，建议在 schema 层拆 `exact_paths` vs `glob_patterns`）。

### Heuristic 扩展（5 exact + 2 prefix）

v2 默认 `UNKNOWN × AGENT × DEFAULT = CONFIRM`，比 v1（默认 ALLOW）严格。这导致
v2 切上来后**多个高频内置工具**（v1 默认 ALLOW、用户从未感觉到 confirm）
开始弹窗：`web_fetch` / `ask_user` / `complete_todo` / 等。

为防止 C6 在生产端出现 UX 雪崩，分类器新增最小必要 heuristic：

| 工具 / 前缀 | ApprovalClass | 来源 |
|---|---|---|
| `web_*` (web_fetch, web_search) | NETWORK_OUT | 惯例：网络只读 |
| `news_*` (news_search) | NETWORK_OUT | 同上 |
| `ask_user` | INTERACTIVE | 用户交互 |
| `exit_plan_mode` | INTERACTIVE | 控制流标志 |
| `task_stop` | INTERACTIVE | 用户控制 |
| `pet_say` / `pet_status_update` | INTERACTIVE | 桌面 UI |
| `send_agent_message` | INTERACTIVE | 多 agent 交互 |
| `complete_todo` | EXEC_LOW_RISK | 标记内部状态 |
| `add_memory` | EXEC_LOW_RISK | KV 写入低风险 |
| `trace_memory` | READONLY_GLOBAL | 读 trace |
| `delegate_to_agent` / `delegate_parallel` | CONTROL_PLANE | trust 模式 ALLOW，default CONFIRM |

完整 tool→class 注册建议在 C7 配合 agent.py 经 `handler.TOOL_CLASSES` 完成（docs §4.21
cookbook）；本表只覆盖最高频"控制 / 内部状态 / 网络读"类工具，避免回归。

### 已知 gap（不影响 C6 上线，记入后续 commit）

1. **plan/ask 模式下 `mode` 没翻译为 v2 `SessionRole`**：
   `permission.check_permission` Step 2 调 `evaluate_via_v2_to_v1_result(...)` 时
   ctx 默认 `SessionRole.AGENT`，没把 `mode='plan'/'ask'/'coordinator'` 透传。
   影响有限（mode_ruleset 在 step 1 拦截大部分 plan 限制），但 v2 在非 agent
   模式下评估的精度会打折。**留待 C7 agent.py 接 ctx 时一并做**。
2. **`_resolve_context` user_message 注入复制 ctx 是 O(n)**：
   生产 hot path 每次 evaluate 复制一份 ctx；ctx 字段不多，开销可控但可优化。
3. **`set_engine_v2` 没 type check**：注入错误类型会在 `evaluate_tool_call` 时
   AttributeError → 被 fail-closed 兜走。安全但不够友好，C8 加 type check。
4. **adapter 内 `_FAIL_CLOSED_TOOL_PREFIXES` 与 `permission.py` 重复定义**：
   理想 single SOT，但跨模块循环 import 风险，妥协可接受 + 已加注释 + smoke
   防 drift。

### 测试结果

```
C6 新增 + 修订:
├─ test_policy_v2_global_engine: 11 PASS
├─ test_policy_v2_adapter:       23 PASS
├─ test_permission_refactor:     10 PASS（mock 点重写）
└─ test_policy_engine_v2:        +4 PASS（path/glob 边界）

整体回归（unit）：
├─ 2564 PASS / 4 SKIP / 8 FAIL
└─ 8 个 FAIL 全部 pre-existing（baseline `git stash` 验证）
   - test_org_setup_tool::TestDeleteOrg::test_delete_*  (cross-pollution)
   - test_reasoning_engine_user_handoff::test_tool_evidence_required_*  (pre-existing)
   - test_remaining_qa_fixes::test_workspace_delete_is_confirmed_even_in_trust_mode  (pre-existing v1 行为漂移)
   - test_remaining_qa_fixes::test_unknown_mcp_write_tool_requires_confirmation  (pre-existing)
   - test_wework_ws_adapter::TestAdapterProperties::test_upload_media_requires_connection  (cross-pollution)

C6 净影响：+1 修复（test_current_turn_grounding 因 web_fetch heuristic 修复），
            0 回归
```

### Smoke 验证（真 `identity/POLICIES.yaml`）

| 场景 | v2 决策 | 是否符合预期 |
|---|---|---|
| trust 模式写桌面 .txt（用户原始投诉） | allow | ✅（user complaint resolved） |
| trust 模式 regedit | allow | ⚠️ 因为 `command_patterns.enabled: false` 在用户 YAML 里 —— 配置驱动行为，非 bug |
| trust 模式写 C:/Windows/System32/x.dll | confirm | ✅（safety_immune match） |
| read_file | allow | ✅ |
| trust 模式 delete_file | confirm | ✅（DESTRUCTIVE 始终 confirm） |
| web_fetch | allow | ✅（新 heuristic） |
| ask_user | allow | ✅（新 heuristic） |
| `permission.check_permission` 端到端 | allow + chain=['policy_engine_v2'] | ✅ |

### 5 维度复审

| # | 维度 | 结论 | 备注 |
|---|---|---|---|
| 1 | 完整性 | ✅ | C6 plan 全做；mode→SessionRole 翻译 known gap 记入 C7 |
| 2 | 架构 | ✅ | 决策/UI 双层清晰；adapter 内 `_FAIL_CLOSED` 重复定义已加注释 + smoke 防 drift |
| 3 | 正确性 | ✅ | DEFER→CONFIRM 降级合理；顺手修了 C5 隐藏的 `_path_under` glob bug |
| 4 | 兼容性 | ✅ | v1 PolicyEngine/PolicyResult/orgs/runtime 全兼容；pre-existing 8 fail 与 C6 无关 |
| 5 | 测试覆盖 | ✅ | 44 个新单测 + 4 个 path/glob 测试；248 PASS，0 regress |

### 关键工程教训

1. **"切干净"是相对概念**：B+X "直切 v2" 听起来比 A+Z "委托" 更干净，但当
   v1 类不仅做决策还做 UI state 时，物理上分不开 = 强行分会污染 C9 的工作。
   "决策切，UI state 不切" 是诚实的过渡架构，不是妥协。
2. **smoke test 用真配置 > 单测**：C5 测试用 `/etc/passwd` literal，过；
   C6 smoke 用 `C:/Windows/**` 真 YAML，立刻暴露 5 个月隐藏的 glob bug。
   测试覆盖率不等于场景覆盖率。
3. **classifier heuristic 是"防 UX 雪崩护栏"**：v2 默认严格的安全策略
   （UNKNOWN→CONFIRM）在切换瞬间会"激活"上百个 v1 默默放行的工具。
   每加一个 heuristic 都是在权衡"安全严格度 vs 用户体验"；在 C7 经 handler
   显式声明 TOOL_CLASSES 之前，heuristic 是必要的过渡兜底。
4. **adapter 是层间契约的物理体现**：v2→v1 的字段冗余写不是丑陋的兼容代码，
   是契约的显式宣告：下游 `metadata.needs_sandbox` 永远可读，无论上游是
   v1 还是 v2。删 adapter 之前必须先迁移所有下游访问形态。

### 下一步

- C7：agent.py RiskGate 切 v2 + replay/trusted_path **消费侧**落地 +
  `mode → SessionRole` 翻译 + `handler.TOOL_CLASSES` 大规模注册
- C8：删旧 policy.py 薄壳 + IM 适配器 owner 判断 + safety_immune 默认 9 类完整接入
- C9：SecurityView 适配 + tool_intent_preview SSE + UI 状态机从 v1 迁出

---

## C6 二轮 audit 修复（2026-05-13 当日）

第一轮 5 维 audit 标 ✅ 之后用户要求"再次确认万无一失"。再做一次更挑剔的
跨模块扫描，发现 **1 个 critical bug + 1 个加固点**：

### Critical：`reset_policy_engine()` 未同步 v2 单例

**症状**：用户在桌面 UI 修改安全配置（trust mode 切换、safety_immune 路径
增删、blocked_commands 改写），后端走 `api/routes/config.py` 的 7 个 endpoint
（`write_security_config` / `write_security_zones` / `write_security_commands`
等），它们写完 YAML 后调 `reset_policy_engine()` 让 v1 重读。

C6 之前这是有效的——v1 是唯一决策源；C6 之后**v1 与 v2 各自缓存配置**：
- v1 重读 YAML → 新 trust mode 生效
- **v2 单例没动，继续按旧 YAML 评估**
- 用户写文件 → permission.check_permission → adapter.evaluate_via_v2
  → 旧 v2 引擎说 CONFIRM → 用户体感"信任模式不生效"
- **完美重现 P1 用户原始投诉**，且**v2 切换让该 bug 仅在 C6 之后才出现**

**修复**（`src/openakita/core/policy.py` `reset_policy_engine()`）：

```python
try:
    from .policy_v2.global_engine import reset_engine_v2
    reset_engine_v2()
except Exception:
    logger.warning("[Policy] failed to reset PolicyEngineV2 singleton; ...")
```

防御性处理：v2 reset 异常**不阻断** v1 reset，只 WARN log（v2 失败比
"配置改完啥都不生效"好）。

**回归测试**（`tests/unit/test_permission_refactor.py`，新增 2 个）：
- `test_reset_policy_engine_also_resets_v2_singleton`：触发 v2 懒加载 →
  reset → 断言 `is_initialized() is False` + 下次 get 拿到新实例
- `test_reset_policy_engine_v2_failure_does_not_break_v1_reset`：patch v2
  reset 抛错 → 验证 v1 reset 仍然完成

**端到端 smoke**（`scripts/c6_audit2_smoke.py`）：
1. set_engine_v2(ALLOW stub) → check_permission(write_file) → behavior=allow ✓
2. reset_policy_engine() → is_initialized()==False ✓
3. set_engine_v2(DENY stub) → check_permission(write_file) → behavior=deny ✓

第 3 步如果 reset 不彻底，会沿用第 1 步的 ALLOW stub → smoke 失败。

### 加固：`_resolve_context` ctx 复制要保留所有字段

**问题**：adapter._resolve_context 在补 `user_message` 时手写复制 PolicyContext
所有字段。如果未来给 PolicyContext 加新字段（如 C7 可能加 `risk_intent_cache`）
而忘记加到这里，会**静默丢失字段**——下游引擎拿不到，调试极困难。

**加固**（`tests/unit/test_policy_v2_adapter.py`，新增 1 个）：
- `test_resolve_context_user_message_copy_preserves_all_fields`：构造一个
  把 11 个 optional 字段全部填值的 PolicyContext，断言复制后所有字段相等。
  未来加字段必须同步改 _resolve_context 才能让该测试过——形成"修改提示"。

### 二轮审视维度

| # | 维度 | 结果 | 备注 |
|---|---|---|---|
| 1 | 模块外调用面扫描 | ✅ → critical bug | 检查所有 `get_policy_engine` 调用点；`reset_policy_engine` 是隐藏断点 |
| 2 | global_engine 线程/race | ✅ | double-checked locking 正确；`get_config_v2` 的 race 在实践中不会触发（assert 仅测试 hint） |
| 3 | adapter 边界 case | ✅ | empty chain → "policy_v2"；None params → {}；DEFER→CONFIRM；glob 边界已修 |
| 4 | orgs/runtime + channels 兼容 | ✅ | monkey-patch 签名不变；UI state 调用全保留 v1 实例 |
| 5 | 测试缺口 | ✅ → 加固 | ctx 复制完整性测试覆盖未来字段扩展 |

### 修复前后对比

| 项目 | 一轮 audit 后 | 二轮 audit 后 |
|---|---|---|
| C6 测试数 | 248 PASS | **251 PASS（+3）** |
| 全量单测 | 2564 PASS / 8 fail | **2567 PASS / 8 fail（同 8 个 pre-existing）**|
| Critical bugs | 0 已知 | **0**（修了 1 个 audit 发现的）|
| docs | C6 实施记录 | **+ 二轮 audit 章节** |

### 关键教训补充

5. **"v2 切上来了"≠"v1 删干净了"**：C6 让两个引擎并存做不同事，
   隐含的 invariant 是"任何让 v1 reload 的入口必须同时让 v2 reload"。
   这种隐性契约在第一轮审视容易漏，必须扫描所有 reset/reload 入口。
   **教训**：跨阶段并存架构必须维护一份"同步点清单"。
6. **"测试通过"≠"问题不存在"**：C6 第一轮所有单测通过、smoke 通过，
   依然漏掉了 reset_policy_engine 这个 production-critical 路径，因为
   单测都自己创建 stub 引擎、不走 reset 路径，smoke 只测决策不测配置变更。
   **教训**：production hot-path 不只决策本身，配置生命周期也算。

---

## 附录 A：重要参考资料

- 主 plan：`security_architecture_v2_31fbf920.plan.md`（1528 行）
- 用户原始投诉日志（plan 中已引用）：`Policy] confirm: write_file — 信任模式下仍需确认高风险操作`
- 现有 6 层安全描述：[`README.md:472-495`](../README.md)（v2 上线后需更新为 12 步 + ApprovalClass 描述）
- 漏洞披露：[`SECURITY.md`](../SECURITY.md)（不动）
- 配置参考：[`docs/configuration.md`](configuration.md)（C18 同步 `--auto-confirm` 漂移）

## C7 实施记录（2026-05-13 完成）

### C7 范围（用户最终选择 c7_full + consume_keep_v1）

- **ContextVar wire**：在 `chat_with_session` 与 `chat_with_session_stream` 两条入口
  设置 `PolicyContext` ContextVar，让本轮所有下游 `evaluate_via_v2` /
  `evaluate_message_intent_via_v2` 拿到与 v1 RiskGate 同源的 ctx
  （confirmation_mode + session_role + replay/trusted_path 快照）。
- **RiskGate 决策切 v2**：`_check_trust_mode_skip` 改为 v1+v2 双查，任一报非 trust
  即不 skip（保守语义，且兼容 test 场景下只 mutate v1）。
- **handler.TOOL_CLASSES 大批量**：为 25+ 主要 handler（覆盖 100+ 工具）补
  显式 ApprovalClass 声明，杜绝启发式回退。
- **rebuild_engine_v2 + explicit_lookup**：`_init_handlers` 末尾把
  `SystemHandlerRegistry.get_tool_class` 注入 v2 engine classifier，让 handler
  显式声明优先于启发式生效。
- **decision_only**：v1 `_consume_risk_authorization` / `_check_trusted_path_skip`
  保持不动（消费/边缘判断仍走 v1，对齐 C6 决策；engine 保持 read-only）。

### 新增文件

| 文件 | 作用 |
|---|---|
| `tests/unit/test_policy_v2_c7_wire.py` | C7 wire 套件（21 个 test，覆盖 ctx builder + msg intent + explicit_lookup + ContextVar lifecycle）|
| `scripts/c7_smoke.py` | 5 个端到端 smoke：trust 桌面写、trust delete、trust read、plan write deny、default read |

### 修改文件

| 文件 | 关键改动 |
|---|---|
| `core/policy_v2/adapter.py` | +`build_policy_context`、`mode_to_session_role`、`evaluate_message_intent_via_v2`；+`_coerce_replay_auths` / `_coerce_trusted_paths` 把 v1 dict 形态归一为 dataclass |
| `core/policy_v2/__init__.py` | 暴露 C7 新 API |
| `core/agent.py` | 两入口 ContextVar set/reset；`_check_trust_mode_skip` 改 v1+v2 双查；`_init_handlers` 末尾调 `rebuild_engine_v2(explicit_lookup=...)` |
| `tools/handlers/*.py` × 25 | 25 个 handler 加 `TOOL_CLASSES` 字典 + `from ...core.policy_v2 import ApprovalClass` |

### 关键设计决策

1. **`_check_trust_mode_skip` 双查（v1 AND v2）**：
   - 初版仅读 v2，破坏了 `test_non_trust_mode_does_not_skip`（test mutate v1
     直接，v2 没刷新）。
   - 终版读 v1 和 v2，**任一显式说"不是 trust" 就不 skip**（保守 + 兼容旧测）。
   - 生产链路 `reset_policy_engine` C6 已同步两层，正常情况下永远一致；
     此双查只在异常路径（admin UI 直改 v1 / 测试 mutate）多一道闸门。

2. **handler.TOOL_CLASSES 25 个文件批量**：
   - 覆盖 filesystem / agent / system / memory / browser / scheduled / mcp /
     skills / persona / profile / desktop / im_channel / todo / web_search /
     web_fetch / mode / notebook / code_quality / search / tool_search /
     plugins / sleep / sticker / lsp / structured_output / opencli /
     cli_anything / agent_package / agent_hub / skill_store / worktree /
     config / org_setup / powershell。
   - 启发式回退（HEURISTIC_PREFIX）保留作兜底，但显式优先；C19 完备性测试
     未来会要求所有内置工具必须显式声明（无启发式回退）。

3. **engine 保持 read-only**：
   - replay/trusted_path 的"消费"（session.set_metadata None 单次清）仍由
     agent.py 的 `_consume_risk_authorization` 完成，engine 决策时只读 ctx。
   - 这样 dry-run 决策可重放、不依赖 session 副作用、与 C5 read-only engine
     设计一致。

4. **build_policy_context fail-soft 全链路**：
   - session 可能是 None / dict / SessionContext / mock，所有 `getattr` /
     `get_metadata` 异常退化为空 list 而非抛异常，保证 ctx 构造永不崩溃
     production 入口。
   - malformed 条目（如 expires_at 不是 float）跳过 + debug log，不让一条
     烂数据废掉整批授权。

5. **evaluate_message_intent_via_v2 fail-soft → CONFIRM**：
   - engine 不可用 → 返回 CONFIRM（让用户决断），不直接 DENY 阻断对话。
   - 与 evaluate_via_v2 的 fail-closed 不同：tool 决策保守 = DENY；msg
     intent 决策保守 = CONFIRM（不阻断对话）。两者各得其所。

### 验证结果

- 新增单测 21 个 + 跑通 ✅（test_policy_v2_c7_wire.py）
- C5/C6 既有单测 108 个 + 全部仍跑通 ✅
- 全量 unit 套（2596 个）：2588 passed + 8 failed（== C6 baseline）+ 4 skipped
- **0 net new regressions**（8 failures 均为 C6 阶段记录的 pre-existing）
- 5 个 smoke 全过 ✅（scripts/c7_smoke.py）
- ruff 0 error ✅

### C7 修复的回归（c7_split-brain 候选）

| # | 问题 | 修复 |
|---|---|---|
| C7-R1 | 初版 `_check_trust_mode_skip` 仅读 v2，导致 `test_non_trust_mode_does_not_skip` 失败：test mutate v1 后 v2 仍读 YAML 中的 `mode: yolo`，函数错返 "trust_mode" | 改 v1+v2 双查，任一显式"不是 trust"立即返回 None；保守 + 测试兼容 |

### 工程教训（C7 二轮）

1. **每个新增 API 必须验证：调用方是否 `monkeypatch` 了内部状态**。`_check_trust_mode_skip`
   的 v1 实现允许测试通过 `engine._config.confirmation = ...` 直接 mutate；C7
   切 v2 后，测试 mutate v1 不再生效，必须双查或迁移测试。
2. **ContextVar 在 finally 里 reset 是必须的**。FastAPI worker 复用 task 的场景
   下，未 reset 的 ContextVar 会让下一轮请求继承上一轮 ctx（safety_immune /
   trust_mode 等关键判断都基于 ctx），可能造成 cross-request leak。
3. **handler 加 TOOL_CLASSES 时务必同步 import**。25 个文件批量改 import 容易
   漏几个；ruff `F401`/`I001` 能自动 catch。
4. **explicit_lookup 必须在 handler 全部注册完后才注入 v2**。早注入会让某些
   handler 还没收集到 TOOL_CLASSES；C7.5 把 `rebuild_engine_v2` 放
   `_init_handlers` 末尾，确保 25 个 handler 全部 register 完才 rebuild。

### C7 二轮 audit（2026-05-13 提交后审查）

按 5 维系统复审 C7 实施，结果：

| 维度 | 检查项 | 结论 |
|---|---|---|
| D1 完整性 | 34 个 handler 是否都加 TOOL_CLASSES，是否都被 registry 收 | ✅ 138/138 tools `via explicit_handler_attr`（脚本：`scripts/c7_audit2_registry_check.py`） |
| D2 架构 | ContextVar 生命周期 / IM channel 透传 / 嵌套 set/reset | ✅ 7 项 ctx-path 检查全过（`scripts/c7_audit2_ctx_paths.py`） |
| D3 不打地鼠 | dual-check `_check_trust_mode_skip` 语义是否退化 / fail-soft 是否过度 | ✅ 6 种 v1×v2 组合枚举验证；`build_policy_context` 在 `BadSession.get_metadata` 抛错时降级到空列表，不掩盖逻辑 bug |
| D4 隐藏 bug | 6 大调用路径（CLI run/serve/interactive/IM/sub-agent/scheduled）覆盖；reset 路径 | **🔴 发现并修复 1 个 P2 bug**（见下） |
| D5 兼容性 | 全量 unit + lint + 旧安全测试 | ✅ 8 baseline failures 不增（pre-existing），2589 passed（+1 新 regression test） |

#### D4 发现并修复：reset_engine_v2() 丢失 explicit_lookup（P2 regression）

**复现**（`scripts/c7_audit2_reset_repro.py`）：

```
[step 2] classify(fake_test_tool) -> mutating_scoped via explicit_handler_attr
[step 4] reset_engine_v2() (simulating UI Save Settings)
[step 6] classify(fake_test_tool) -> unknown via fallback_unknown   ← 修复前
```

**根因**：`api/routes/config.py` 有 8 处 `reset_policy_engine()` 调用（每次用户在
高级设置里点 Save 都会触发），它会调 `reset_engine_v2()` 把 `_engine = None`。
下次工具调用时 `get_engine_v2()` 懒加载，走 `_build_default_engine(explicit_lookup=None)`
→ classifier 失去 handler 注册表显式查表 → **138 个工具退回到启发式分类**。
启发式精度比显式低，部分边界 case（如 `setup_organization` / `delegate_to_agent`）
可能错判 ApprovalClass，进而错判审批策略。

**修复**（`src/openakita/core/policy_v2/global_engine.py`）：

- 新增模块级 `_explicit_lookup` 缓存，`rebuild_engine_v2` 在 lock 内持久化它。
- `_build_default_engine` 在 caller 没传时回退到模块缓存。
- `reset_engine_v2()` 默认**保留** `_explicit_lookup`；测试 fixture 需要彻底
  reset 时显式传 `clear_explicit_lookup=True`。
- 新增 `tests/unit/test_policy_v2_c7_wire.py::test_explicit_lookup_survives_reset`
  锁定回归。

修复后：

```
[step 2] classify(fake_test_tool) -> mutating_scoped via explicit_handler_attr
[step 4] reset_engine_v2() (simulating UI Save Settings)
[step 6] classify(fake_test_tool) -> mutating_scoped via explicit_handler_attr
[PASS] explicit_lookup preserved across reset
```

#### D4 已知缺口（不属于 C7，留给 C12）

`execute_task` / `execute_task_from_message` 路径（**scheduled task**、CLI
`openakita run "..."` 一次性任务、`evolution/self_check.py` 自我修复循环）
不安装 ContextVar，下游走 `_build_fallback_context`：

| 字段 | fallback 取值 | C7 行为是否正确 |
|---|---|---|
| workspace | `Path(os.getcwd())` | ✅ 一致 |
| session_role | `AGENT` | ✅ 一致 |
| confirmation_mode | `get_config_v2().mode` | ✅ 跟 trust mode / strict 对齐 |
| replay_authorizations / trusted_path_overrides | 空 | ✅ scheduled 任务本就无 UI 授权 |
| **is_unattended** | **False** | ⚠️ scheduled 应该是 True，C12 来 wire |

`is_unattended=False` 的影响是：scheduled task 遇到 CONFIRM 时会按"等用户回应"
处理 → 任务挂起。这是**已存在于 C7 之前**的行为（C5 才加的 unattended 策略），
**不是 C7 引入的回归**。`execute_task` 已加 docstring TODO 标记 C12 入口。

#### 二轮额外清理

- 简化 `chat_with_session` / `chat_with_session_stream` 中 `channel=` 表达式，
  去掉 `if session is not None else "desktop"` 死分支（`getattr(None, "channel",
  None)` 本就安全）。
- ruff lint 0 error。

可放心进入 C8。

## C8a 实施记录（2026-05-13 完成）

### C8 范围分拆与最终选择

C8 调研期初步包含 7 个 sub-task，但与 C9（SecurityView 重建）有强依赖：
- **#6 v1 RiskGate 删除** 需要 C9 的 SecurityView 完成 `pending_approval`
  迁移后才能安全摘除（否则 IM owner 审批 / desktop confirm 会失去去重屏障）。
- **#7 删除 src/openakita/core/policy.py** 同样需要 C9 完成 `prepare_ui_confirm`
  / `wait_for_ui_resolution` 的 v2 化（v1 engine 当前仍是 SSE 等待中枢）。

用户最终选择 **C8a = #1–#5**（不动决策中枢），#6/#7 推迟到 C9 完成后作为 C8b
独立 commit。本次 C8a 五项均为**非破坏性补强**（additive defaults / 配置驱动 /
新字段 / 真删过期 / SSE bug 修复），无 v1→v2 决策权切换，回归风险最低。

### 五项 sub-task 与改动

| # | 标题 | 关键文件 | 摘要 |
|---|---|---|---|
| #1 | safety_immune 9 类精细路径接入 POLICIES.yaml | `core/policy_v2/safety_immune_defaults.py`（新）+ `engine.py` | 9 类 builtin 路径（identity/audit/keyring/git/tauri/python venv/node_modules/system/lockfile）通过 `expand_builtin_immune_paths()` 与用户配置 **加性 union**，永远兜底保护 |
| #2 | OwnerOnly 配置驱动 + IM owner 接入 ctx | `channels/gateway.py` + `api/routes/im.py` + `core/policy_v2/adapter.py` | IM `_handle_message` 注入 `session.metadata["is_owner"]`；新增 `/api/im/owner-allowlist` 持久化（`data/sessions/im_owner_allowlist.json`）；`build_policy_context` 透传 `is_owner` 给 OwnerOnly 决策 |
| #3 | switch_mode 真生效 | `sessions/session.py` + `tools/handlers/mode.py` + `adapter.py` | Session dataclass 新增 `session_role: str = "agent"` + `confirmation_mode_override: str | None = None`；`switch_mode` 写 `session.session_role`；`build_policy_context` 优先读 session 字段覆盖默认 |
| #4 | consume_session_trust 真删过期规则 | `core/trusted_paths.py` | 调用时 split `surviving / pruned`，pruned 非空时 `session.set_metadata(SESSION_KEY, surviving)` 持久化（之前只是 in-memory 跳过，元数据无界增长） |
| #5 | IM 前缀 conversation 早退不 yield SSE | `core/reasoning_engine.py` + `core/policy.py` + `channels/gateway.py` | 删除两处 `_is_im_conversation` 早退分支；IM 通道 `_confirm_timeout = max(orig*4, 180s)`；SSE event 加 `"channel": "im" / "desktop"`；`prepare_ui_confirm` 改幂等（避免 gateway 与 reasoning_engine 互踩 asyncio.Event） |

### 新增文件

| 文件 | 作用 |
|---|---|
| `core/policy_v2/safety_immune_defaults.py` | 9 类 builtin 路径常量 + `expand_builtin_immune_paths()`（解析 `${CWD}` / `~`） |
| `tests/unit/test_policy_v2_c8_wire.py` | 12 个 test，覆盖 5 项 sub-task 的核心断言 + 边界 |
| `scripts/c8_audit_d1_completeness.py` | D1 完整性审计 |
| `scripts/c8_audit_d2_architecture.py` | D2 架构正确性审计 |
| `scripts/c8_audit_d3_no_whack_a_mole.py` | D3 不打地鼠（独立性 / fail-safe / 无隐藏耦合） |
| `scripts/c8_audit_d4_hidden_bugs.py` | D4 隐藏 bug 探针（CWD / from_dict 健壮性 / 元数据写入 / round-trip） |
| `scripts/c8_audit_d5_compat.py` | D5 兼容性（旧 sessions.json / v1 yaml 迁移 / v1 API / 独立 ACL 文件 / 默认构造 smoke） |

### 关键设计决策

1. **builtin safety_immune 加性 union（不是覆盖）**：用户在 `POLICIES.yaml`
   里配的 `safety_immune.paths` 与 9 类 builtin 取并集，且 builtin 永远在
   前——即使用户配置为空 list，9 类系统关键路径仍受保护。这是"安全加性"
   原则：用户能放宽自己的代码区，但**不能关掉系统底线**。

2. **`Session.from_dict` 三重健壮性**（D4 探针专门验证）：
   - 缺字段 → 默认值（`session_role="agent"`, `confirmation_mode_override=None`）
   - 空字符串 / 错误类型 → fallback 到默认
   - 旧 sessions.json 直接反序列化即可，无需 migration 脚本

3. **`prepare_ui_confirm` 幂等**：原实现每次都新建 `asyncio.Event`，导致
   gateway（IM 渠道）与 reasoning_engine 同时 prepare 同一 confirm_id 时
   后注册者覆盖前者的 event，前者 `wait_for_ui_resolution` 永远超时。
   改为：若已存在 event 且 decision 未到，**复用**已有 event。

4. **IM confirm timeout 4×（最少 180s）**：桌面默认 60s 对 IM 用户太短
   （需要切群、看通知、审阅 card）。`max(orig*4, 180s)` 给到至少 3 分钟，
   同时保留管理员调长 `confirm_timeout` 时的倍数关系。

5. **OwnerOnly 在 v2 engine 内决策，gateway 只负责注入 `is_owner`**：
   gateway 通过 `_get_owner_user_ids` + `_apply_persisted_owner_allowlist`
   读 `im_owner_allowlist.json` 解析当前消息发送者是否 owner，写入
   `session.metadata["is_owner"]`；engine 决策时 `build_policy_context`
   透传，`OwnerOnly` 工具被非 owner 调用时 → DENY。**职责分离**：
   gateway 不懂决策、engine 不懂 IM。

6. **`consume_session_trust` 真删 vs in-memory skip**：原实现在迭代时遇
   到 expired 跳过，但**留在 metadata 里**。长 session 下规则数无界增长，
   且每次 trust 检查 O(n) 扫描成本递增。新实现 in-place pruning，仅在
   pruned 非空时写一次 metadata，避免无谓 IO。

### 修改文件清单

| 文件 | 关键改动 |
|---|---|
| `core/policy_v2/engine.py` | `__init__` 接 `expand_builtin_immune_paths()` + 用户配置 union |
| `core/policy_v2/__init__.py` | 暴露 `expand_builtin_immune_paths` / `BUILTIN_SAFETY_IMMUNE_PATHS` |
| `core/policy_v2/adapter.py` | `build_policy_context` 读 `session.session_role` / `confirmation_mode_override` / `metadata["is_owner"]` |
| `core/trusted_paths.py` | `consume_session_trust` 真删过期 + 持久化 |
| `core/policy.py` | `prepare_ui_confirm` 幂等（已存在 event 则复用） |
| `core/reasoning_engine.py` | 删 IM 早退分支 × 2；IM `_confirm_timeout` 4× / ≥180s；SSE event 加 `channel` 字段 |
| `channels/gateway.py` | `_handle_im_security_confirm` 不再消费 resolution（只渲染 + 转发选择，实际 wait 由 reasoning_engine 处理）；新增 `_get_owner_user_ids` + `_apply_persisted_owner_allowlist`；`_handle_message` 写 `session.metadata["is_owner"]`；`start()` 调 `_apply_persisted_owner_allowlist()` |
| `api/routes/im.py` | 新增 `GET/POST /api/im/owner-allowlist` + `_load_owner_allowlist` / `_save_owner_allowlist`（`data/sessions/im_owner_allowlist.json`，`None=未配置`，`[]=显式锁定`） |
| `sessions/session.py` | 新增 `session_role: str = "agent"` + `confirmation_mode_override: str | None = None`；`to_dict` / `from_dict` 加序列化 + 三重健壮性 |
| `tools/handlers/mode.py` | `_switch_mode` 改写 `session.session_role`（原写不存在的 `session.mode`，C8 之前**完全失效**） |
| `tests/unit/test_policy_engine_v2.py` | 边界 path test 改用 `/private_test_lab/ssh` / `D:/TestLab/OpenAkita` 等合成路径，避免与 builtin `/etc/**` 冲突 |

### 5 维 audit 结果

| 维度 | 检查项数 | 结论 |
|---|---|---|
| D1 完整性 | safety_immune 9 类 / OwnerOnly wire / session_role 字段 / consume 删除 / IM SSE yield | ✅ 全过 |
| D2 架构正确性 | builtin 加性 union / `is_owner` 透传链 / `session_role` 优先级 / `prepare_ui_confirm` 幂等 / IM confirm fanout | ✅ 全过 |
| D3 不打地鼠 | 5 项独立性（互不耦合）/ fail-safe（缺字段降级）/ 无隐藏副作用（gateway 不消费 resolution）| ✅ 全过 |
| D4 隐藏 bug | CWD 展开 / `is_owner` 默认 / `from_dict` 健壮性 / 不写 spurious metadata / safety_immune 多次实例化稳定 / owner_allowlist round-trip（临时文件路径，不污染生产） | ✅ 全过 |
| D5 兼容性 | 旧 sessions.json 反序列化 / v1 POLICIES.yaml 迁移 + builtin union / v1 PolicyEngine API / `group_policy.json` ⊥ `im_owner_allowlist.json` / 默认构造 smoke | ✅ 全过 |

### 验证结果

- C8 wire 单测 12 个 ✅（`tests/unit/test_policy_v2_c8_wire.py`）
- 全量 unit 套（2622 个）：2614 passed + 8 failed（== C6/C7 baseline）+ 4 skipped
- **0 net new regressions**（8 failures 均为 C6/C7 阶段记录的 pre-existing）
- 5 个 audit 脚本（D1–D5）全过 ✅
- ruff（C8 触及文件 100% pass）✅

### C8a 修复的回归 / 隐藏 bug

| # | 问题 | 修复 |
|---|---|---|
| C8-R1 | `switch_mode` 工具写 `session.mode`（不存在的字段），实际**完全失效**——LLM 切换 plan/ask 模式后 PolicyContext 仍按 agent 决策 | Session 加 `session_role` 字段，`switch_mode` 改写新字段，`build_policy_context` 优先读 |
| C8-R2 | IM 渠道 `_handle_im_security_confirm` 与 reasoning_engine 互相 `prepare_ui_confirm` 同一 confirm_id，**后者覆盖前者的 asyncio.Event**，gateway 永远等不到 resolution | gateway 不再 prepare/wait，只渲染卡片转发选择；`prepare_ui_confirm` 幂等保险 |
| C8-R3 | IM 对话遇 confirm 直接打印 "无法安全完成交互式确认" 早退，**SSE event 不 yield**，gateway 拿不到事件就无法发卡片 | 删除两处早退分支，统一走 yield；IM 通道 timeout 拉长到 ≥180s |
| C8-R4 | `consume_session_trust` 遇过期规则只跳过不删，session.metadata 中规则无界增长 | 真删过期 + 持久化，仅在 pruned 非空时写 1 次 |
| C8-R5 | OwnerOnly 工具策略**完全无人调用 is_owner**——engine 有判断逻辑但 gateway 从不写入 `session.metadata["is_owner"]`，等于永远 False（任何人都拒）或永远 True（取决于默认） | gateway 注入 + adapter 透传 + `im_owner_allowlist.json` 持久化 |

### 工程教训（C8）

1. **"配置项存在 ≠ 配置项生效"**——`OwnerOnly` 在 `PolicyConfigV2` 里
   定义了一年，没人调；`switch_mode` 工具改 `session.mode` 字段一年，
   字段根本不存在。验收 v2 配置时必须**反向追**：从决策点出发，确认
   每个配置项都有调用方。
2. **builtin defaults 必须加性，不能覆盖**——用户配置 `safety_immune.paths: []`
   时，builtin 仍生效。"用户配置覆盖默认" 是常识，但安全场景反过来：
   "默认覆盖用户" 才是安全加性。
3. **IM confirm timeout 桌面同款 = bug**——人在桌面前 60s 够看 dialog，
   人在手机/工位散步时 60s 不够看群消息。timeout 应**按渠道分类**，不按
   "全局默认" 分。
4. **gateway 与 engine 不要双重消费 SSE event**——同一 confirm_id 只能
   有一个 owner 处理 resolution，否则资源竞争。C8 把所有权统一在
   reasoning_engine（最先 yield 的那个），gateway 只是中继。
5. **`Session.from_dict` 必须假设 payload 是脏的**——旧 sessions.json /
   人工编辑 / 字段类型漂移都可能让 `from_dict` 崩。新字段必须三重防御
   （缺字段 / 错类型 / 空值）+ 默认。

### 推迟到 C8b 的项（C9 完成后）

- **#6 删除 v1 RiskGate**（`agent.py` 中的 `_check_trust_mode_skip` /
  `_consume_risk_authorization` / `_check_trusted_path_skip`）：当前仍是
  pre-LLM 闸门 + replay 消费的执行点，C9 SecurityView 接管后再砍。
- **#7 删除 `src/openakita/core/policy.py`**：当前仍是 SSE 等待中枢
  （`prepare_ui_confirm` / `wait_for_ui_resolution` / `cleanup_ui_confirm`），
  C9 把这三个函数迁到 SecurityView 后才能安全删除。

可放心进入 C9（SecurityView 重建），C8b 在 C9 完成后作为独立 commit。

---

## C9 实施记录（2026-05-13 完成，scope = C9a + C9b）

### 决策：scope 收敛为 C9a + C9b（C9c 推迟到 C12）

C9 文档原范围 = §8 SSE 字段 + R2-11 `tool_intent_preview` + R5-20 dry-run preview + UI 状态机迁出。
为避免重蹈 C8 的"7 sub-task 杂烩"覆辙，本轮把 C9 切成 3 块，由用户选定执行 **C9a + C9b**：

- **C9a — SecurityView v2 适配（用户可见价值，低风险）**：4 个 sub-task，
  全部独立、可向后兼容。
- **C9b — UI confirm bus 抽出（C8b 前置依赖）**：3 个 sub-task，
  把 `_pending_ui_confirms` / `_ui_confirm_events` / `_ui_confirm_decisions`
  从 `core/policy.py` 搬到 `core/ui_confirm_bus.py`，让 C8b 能安全删 v1 RiskGate。
- **C9c — 新增 SSE 事件（推迟）**：`tool_intent_preview` /
  `pending_approval_created/resolved` / `policy_config_reloaded[_failed]`
  这些事件多数是为 C12（计划任务/无人值守审批）服务的，单独提交价值低，
  与 C12 一起做职责更聚焦。

### C9a 实施细节

#### C9a-1 SSE 事件向后兼容新增 v2 字段

`reasoning_engine.py` 两个 `security_confirm` yield 站点（`execute_batch` 早路径与
非 batch 路径，行 ~4418 / ~4811）都补上：

```python
"approval_class": _pr.metadata.get("approval_class"),  # v2 11 维分类
"policy_version": 2,                                    # 区分 v1 兜底 vs v2 主决策
```

向后兼容：旧前端读不到 `approval_class` 就会落到原 `risk_level` 路径——
新增字段是纯加法。`approval_class` 在 C6 已经由 `policy_v2/adapter.py` 写入
`PolicyResult.metadata`，C9a-1 只是把它转发到 SSE。

#### C9a-2 SecurityConfirmModal + ChatView 渲染 v2 字段

- `apps/setup-center/src/views/chat/utils/chatTypes.ts` —— `security_confirm`
  事件类型加 `approval_class?` / `policy_version?` / `channel?` 三字段
- `apps/setup-center/src/views/ChatView.tsx` —— `SecurityConfirmData` 类型加
  `approvalClass / policyVersion / channel`，从 SSE event 透传
- `apps/setup-center/src/views/chat/components/SecurityConfirmModal.tsx` ——
  新增 `APPROVAL_CLASS_LABELS` 中英映射（11 维分类 → 中文 + 颜色），
  在 modal header 渲染语义 badge；同时把 `channel === "im"` 也作为 IM 渠道标识
  显示（IM 用户更需要知道这是远端来源）

向后兼容：`approvalClass` 缺失时不渲染 badge，旧 backend 完全不受影响。

#### C9a-3 SecurityView IM owner allowlist UI

- 新增 `imowner` 标签页 + `ImOwnerChannelRow` 子组件
- 新增 GET 流程：先调 `/api/im/channels` 列出已启用渠道，再 fan-out 调
  `GET /api/im/owner-allowlist?channel=...`（C8a 已上线后端）
- 新增三态 UI：未配置（is_owner=true 默认）／已配置空列表（CONTROL_PLANE 全员被拒）／
  非空列表（仅列内 user_id 可用 CONTROL_PLANE 工具）
- "清除"按钮二次点击确认（防误操作）；"保存"前 textarea diff 状态判断 dirty

#### C9a-4 dry-run preview（R5-20）

新增 `POST /api/config/security/preview` 后端：

- `body=None/{}` → 用当前 persisted config（`get_engine_v2()`）
- `body={"security": {...}}` → 通过 `load_policies_from_dict` 临时构建 ad-hoc
  `PolicyEngineV2(config=cfg)`，**不写盘、不替换全局 singleton**

固定 9 个样本工具（read_file / write_file / write_file→/etc/passwd /
write_file→identity/SOUL.md / delete_file / run_shell ls / run_shell rm -rf / /
delegate_to_agent / switch_mode），返回每个工具的 `decision / approval_class /
risk_level / safety_immune_match`。

前端新增 `dryrun` 标签页，渲染表格 + immune badge + 重新运行按钮。
首次打开 tab 自动加载，后续点按钮触发。

### C9b 实施细节

#### C9b-1 抽 `core/ui_confirm_bus.py`

新模块定义 `UIConfirmBus` 类：

| 方法 | 职责 |
|---|---|
| `configure_ttl(s)` | 由 PolicyEngine 推入 confirm_ttl 配置 |
| `store_pending(id, name, params, *, session_id, needs_sandbox)` | SSE 派发前注册 sidecar |
| `prepare(id)` | 注册等待 Event（idempotent，C8a §2.3 修复同款语义） |
| `cleanup(id)` | 同时清 event + decision |
| `resolve(id, decision)` | 唤醒 waiter + 返回 pending sidecar 给调用方 |
| `wait_for_resolution(id, timeout)` | 阻塞等待，超时 deny 兜底（同时 cleanup orphan） |
| `cleanup_session(session_id)` | 清 session 内所有 pending |
| `_cleanup_expired()` | TTL GC |

`get_ui_confirm_bus()` 模块级 singleton；`reset_ui_confirm_bus()` 仅供 test 用。

**关键设计：bus 对 v1 `mark_confirmed` 零依赖**。`resolve` 只返回 pending sidecar
（含 normalize 后的 decision + 计算后的 needs_sandbox），让调用方决定要不要做
v1 mark_confirmed。这样 C8b 删 v1 RiskGate 时，`mark_confirmed` 消失，bus 不需要任何修改。

#### C9b-2 reasoning_engine 改用 bus

`reasoning_engine.py` 两个 hotspot 都补 `_bus = get_ui_confirm_bus()`，把：

- `_pe.store_ui_pending(...)` → `_bus.store_pending(...)`
- `_pe.prepare_ui_confirm(...)` → `_bus.prepare(...)`
- `_pe.wait_for_ui_resolution(...)` → `_bus.wait_for_resolution(...)`
- `_pe.cleanup_ui_confirm(...)` → `_bus.cleanup(...)`

但 **gateway / cli/stream_renderer / channels/adapters/{telegram,feishu} /
api/routes/config** 的 `pe.resolve_ui_confirm(...)` 调用**保持不变**：
这些位置需要触发 v1 `mark_confirmed`（写 `_session_allowlist` + 持久化
allowlist），bus 本身不能直接做（避免 v1 反向耦合）。等 C8b 删 v1 后，
这些 callsite 会自然迁移到 `bus.resolve(...)` 一行。

#### C9b-3 facade 兼容层

`policy.py` 上原来的 6 个方法（`store_ui_pending` / `prepare_ui_confirm` /
`cleanup_ui_confirm` / `wait_for_ui_resolution` / `resolve_ui_confirm` /
`cleanup_session`）全部改为 thin facade，内部 import 并委托给
`get_ui_confirm_bus()`。

`resolve_ui_confirm` facade 多做一步：拿到 bus 返回的 pending sidecar 后，
按 decision scope（once/session/always）调 `self.mark_confirmed(...)`。
这是 v1-only 的桥接逻辑，C8b 之后可以删掉整个 facade。

**回收的死代码**：`reset_policy_engine` 末尾原本有
`refreshed._pending_ui_confirms = previous._pending_ui_confirms` 等三行 field-by-field
copy，是 C7 修复"engine reset 时 SSE 等待状态丢失"的补丁。bus 是 module-level
singleton **天然存活 reset**，所以这三行直接删掉。

### 5 维 audit 结果（含新增 `scripts/c9_audit.py`）

| 维度 | 关注点 | 结果 |
|---|---|---|
| **C9-D1 完整性** | 每个 sub-task 都在 prod 路径上可达；SSE 字段 / SecurityView tab / bus 模块 / reasoning_engine 接线全部存在 | ✅ 6 项 PASS |
| **C9-D2 架构** | PolicyEngine 不再持有 `_ui_confirm_*` 三字段；6 个 facade 全部 delegate；reset_policy_engine 不再 copy bus 字段（singleton 天然存活） | ✅ 3 项 PASS |
| **C9-D3 单源** | 3 个 bus 状态字典（`_events` / `_decisions` / `_pending`）在整个 src tree **各只 assign 1 次**；policy.py 不再有 legacy 类型声明 | ✅ 2 项 PASS |
| **C9-D4 隐藏 bug** | bus 存活 engine reset；prepare 幂等；timeout deny 同时 cleanup orphan pending；resolve 无 pending 时仍唤醒 waiter；facade 仍触发 mark_confirmed；dry-run preview 不替换 global singleton | ✅ 6 项 PASS |
| **C9-D5 兼容** | 外部 `resolve_ui_confirm` callers 通过 facade 仍工作；旧 SSE consumer（无 approval_class）能读 legacy 字段 | ✅ 2 项 PASS |

C8 的 D1-D5 audit 同步更新（`scripts/c8_audit_d1_*.py` / `_d3_*.py` 改读 bus），
**5 维全 PASS**，C8a 之前所有不变量继续守住。

### 测试结果

- `pytest tests/unit/` 全量：2615 passed / 8 failed / 4 skipped
- 8 失败 = C8a 完成时的同批 pre-existing failures（`test_org_setup_tool` /
  `test_reasoning_engine_user_handoff` / `test_remaining_qa_fixes` /
  `test_wework_ws_adapter`），全部与 UI confirm / SSE 无关
- **stash + replay 验证**：把 C9 改动 stash 出去，pre-C9 baseline 同样 8 个
  failure；把 stash 还原后，独立运行那 8 个 test 都通过。**净新增 C9 回归 = 0**
- 5 维 audit：C8 D1-D5 + C9 D1-D5 共 10 个 audit 全 PASS
- ruff：所有改动文件 PASS

### 给 C8b 留的接口

1. `core/policy.py` 的 6 个 facade 方法都已经是薄壳，可以直接 `git rm`
   策略性地删除（外部 callers 改 import 到 `get_ui_confirm_bus()`）
2. `mark_confirmed` 是 v1 RiskGate 的一部分，删它前先把 `gateway.py` /
   `cli/stream_renderer.py` / `channels/adapters/{telegram,feishu}.py` /
   `api/routes/config.py:1793` 这 5 处的 `pe.resolve_ui_confirm(...)`
   改为 `get_ui_confirm_bus().resolve(...)`（不要 `mark_confirmed` 的返回值
   即可——bus.resolve 已返回 pending sidecar）
3. `_pending_ui_confirms` / `_ui_confirm_events` / `_ui_confirm_decisions`
   字段已经从 PolicyEngine 移除，C8b 删 policy.py 时不需要做任何 state migration

### 工程教训

1. **scope 切分自始至终**：C9 一开始就拆成 a/b/c 三块，让用户选；不要等做到一半
   再发现"这个 commit 太大了"。
2. **facade 模式作为渐进迁移工具**：bus 出生时同时保留 PolicyEngine 旧 method
   作为 thin facade，所有外部 caller 零代码改动；新 caller（reasoning_engine）
   主动迁移到 bus 验证可用性。等 C8b 才统一切换 + 删除。
3. **decoupling 优先于完美**：bus.resolve 返回 pending sidecar 而不是直接调
   mark_confirmed，避免 v1→bus 反向耦合，让 C8b 真正能干净删 v1。
4. **audit 也要 maintain**：C9b 改了字段位置，C8 的 D1/D3 audit 立刻就 break；
   audit 不是写完就完事，是 living spec，每次架构变化都要同步。

C9 完成，可以进入 **C8b（删 v1 RiskGate + 删 `core/policy.py`）**，
随后是 **C10**（Hook 来源分层 + Trusted Tool Policy）。

---

## C8b 粒度化执行计划（recon-only · 不改生产代码）

> 本节是 **C8b 实施前的调研产物**，不含代码改动。目的是把"删 v1"
> 这个看起来一句话的任务拆成 5 个独立 commit，每个独立可 rollback、
> 风险显式可见。
>
> 起因：用户 review 发现 C8/C9 一开始定义不清晰，差点出现"删了 RiskGate
> 但 PolicyEngine 还活着"或"v2 stub 没填就动 v1"的尴尬中间态。
>
> 输出于：C9 完成后、C8b 开工前。

### §A — `core/policy.py` 出口符号清单（1766 行）

按用途分组（删 v1 时这就是迁移单元）：

| 组 | 符号 | 主要消费者 | 数量 |
|---|---|---|---|
| **A. 决策入口** | `PolicyEngine` class（`assert_tool_allowed` / `_check_*` 等 30+ 方法）| `reasoning_engine`（已切 v2 adapter）/ `permission` shim | 1 个类 ~1330 行 |
|  | `get_policy_engine` / `set_policy_engine` / `reset_policy_engine` | 25+ callsite | 3 函数 |
|  | `PolicyResult` dataclass | reasoning_engine / tool_executor 直接 import | 1 |
|  | `PolicyDecision` enum | reasoning_engine / tool_executor 直接 import | 1 |
| **B. 配置常量** | `_default_protected_paths` / `_default_forbidden_paths` / `_default_controlled_paths` | `config.py` (× 2) | 3 |
|  | `_DEFAULT_BLOCKED_COMMANDS` | `config.py` (× 1) | 1 |
|  | `SecurityConfig` / `ConfirmationConfig` / `SandboxConfig` 等 dataclass | v2 已有等价；纯遗留 | ~10 |
| **C. UI confirm facade**（C9b 后已为薄壳） | `store_ui_pending` / `cleanup_session` / `resolve_ui_confirm` / `prepare_ui_confirm` / `cleanup_ui_confirm` / `wait_for_ui_resolution` | 7 处外部 caller（IM × 2 / CLI / config / chat / gateway × 2） | 6 |
| **D. UserAllowlist CRUD** | `mark_confirmed` / `_save_user_allowlist` / `remove_allowlist_entry` / `get_user_allowlist` / `_check_allowlists` / `_check_persistent_allowlist` | `security_actions.py` × 4 / `tool_executor.py:810` | 6 |
| **E. Skill allowlist** | `add_skill_allowlist` / `remove_skill_allowlist` / `clear_skill_allowlists` / `_is_skill_allowed` | `skills.py` × 4 / `agent.py:2463` | 4 |
| **F. Death switch / readonly mode** | `readonly_mode` 属性 / `reset_readonly_mode` / `_consecutive_denials` / `_total_denials` | `config.py:1903` / `security_actions.py:55` | 4 |
| **G. Frontend mode shim** | `_frontend_mode` 字段 | `config.py:1700-1733` permission-mode API | 1 |

> ⚠️ `channels/gateway.py:2754` 的 `from .policy import GroupPolicy*` 是
> `channels/policy.py` 不是 `core/policy.py`，**与 C8b 无关**，跳过。

### §B — Callsite × Method 矩阵（v1 vs v2 能力对比）

按"v2 是否已具备等价能力"分类：

#### B1. v2 **已等价** —— 可直接换 import（low effort）

| 文件:行 | 调用 | 替换为 |
|---|---|---|
| `agent.py:5760` | `_pe.cleanup_session()` | `get_ui_confirm_bus().cleanup_session()` |
| `chat.py:401` | `pe.cleanup_session()` | 同上 |
| `cli/stream_renderer.py:305-306` | `engine.resolve_ui_confirm()` | `bus.resolve()`（需先在 bus 上加 mark_confirmed-equivalent — 见 D 节）|
| `config.py:1792` | `engine.resolve_ui_confirm()` | 同上 |
| `gateway.py:4778, 4842` (× 2) | `pe.resolve_ui_confirm()` | 同上 |
| `telegram.py:700` | `get_policy_engine().resolve_ui_confirm()` | 同上 |
| `feishu.py:1090` | （读取 `_is_trust_mode`/类似）| `get_config_v2().confirmation.mode == TRUST` |
| `checkpoint.py:250` | `engine.config.checkpoint` | `get_config_v2().checkpoint` |
| `audit_logger.py:113` | `engine.config.self_protection.audit_path` | `get_config_v2().self_protection.audit_path` |
| `config.py:1466,1598,1641,1687,1871,1951` (× 6) | `reset_policy_engine()` | `reset_engine_v2()` |
| `config.py:1560` | `_default_protected_paths` / `_default_forbidden_paths` | 移到 `policy_v2/defaults.py` |
| `config.py:1610` | `_DEFAULT_BLOCKED_COMMANDS` | 同上 |

#### B2. v2 **部分等价** —— 需要 v2 补 method 才能换（medium effort）

| 文件:行 | 调用 | v2 缺什么 |
|---|---|---|
| `agent.py:871` | `engine._is_trust_mode()` (RiskGate fallback) | v2 有 ConfirmationMode.TRUST 但没有 `is_trust_mode()` 便捷函数；可加 helper 或 inline 比较 |
| `agent.py:2463` | `engine.clear_skill_allowlists()` | v2 完全无 skill_allowlist 概念 |
| `skills.py:306, 837` (× 2) | `engine.add_skill_allowlist(skill_id, tools)` | 同上 |
| `skills.py:925, 1003` (× 2) | `engine.remove_skill_allowlist(skill_id)` | 同上 |
| `tool_executor.py:807-810` | `_confirm_cache_key` + `mark_confirmed` (retry-allow) | v2 没有 confirmed_cache 概念 |
| `config.py:1903` | `pe.readonly_mode` | v2 `_check_death_switch` 是 stub return None |
| `security_actions.py:11, 18, 38, 55` (× 4) | `get_user_allowlist` / `remove_allowlist_entry` / `_save_user_allowlist` / `reset_readonly_mode` | v2 配置里有 user_allowlist 但**无运行时 CRUD API**；reset_readonly_mode 同 readonly_mode |

#### B3. v2 **完全无等价** —— 需要先在 v2 上**新增功能**（high effort）

| 功能 | 当前 v1 实现 | 删除前必须做 |
|---|---|---|
| skill allowlist 注入 | `_skill_allowlists: dict[skill_id → set[tool]]` 字段 + 运行时 add/remove + `_is_skill_allowed` 纳入 `_check_allowlists` | v2 加 `SkillAllowlistManager`（可独立模块）+ 在 `_check_user_allowlist` step 集成 |
| user allowlist 持久化 | `_save_user_allowlist()` 写 `identity/POLICIES.yaml` | v2 加 `policy_v2/yaml_writer.py` 或在 loader 上加 round-trip 写入 |
| `_check_user_allowlist` step | v1 `_check_allowlists` + `_check_persistent_allowlist` 完整逻辑 | v2 engine.py:748 stub return None **必须先填实** |
| `_check_death_switch` step | v1 连续 deny → readonly_mode 切换 + `_consecutive_denials` 计数 | v2 engine.py:756 stub return None **必须先填实** + 加 ContextVar 或 PolicyEngineV2 字段持有计数 |
| confirmed_cache（retry-allow） | `mark_confirmed` 写 cache，`_confirm_cache_key(tool, params)` 查 cache | **决策保留与否**：方案 A 在 v2 加同等机制；方案 B 删除（每次 retry 重新决策）；方案 C 仅保留 session_allowlist 部分 |

### §C — 3 个 v1 RiskGate 函数 — 删除前提

`agent.py:750-936` 的 3 个函数实现 pre-LLM 闸门，删除依赖关系：

| v1 函数 | LOC | v2 已实现的部分 | v2 缺的部分（删前必补）|
|---|---|---|---|
| `_consume_risk_authorization` | 81 | v2 `_check_replay_authorization`（read-only，已 wired in C7）| chat handler 仍需把 `risk_authorized_intent` 注入 `PolicyContext.replay_authorizations`；mutation 侧（清空 metadata）保留在 chat handler 即可 |
| `_check_trust_mode_skip` | 49 | v2 matrix `(role, mode, class) → action` 已覆盖 trust 语义 | **风险点**：v1 用 `RiskIntentResult.target_kind` 区分 5 种"敏感 target 仍 confirm"；v2 用 `ApprovalClass` + `_check_safety_immune` 复合达成。需 audit 验证 5 个 v1 必 confirm target（`SECURITY_USER_ALLOWLIST` / `SECURITY_POLICY` / `DEATH_SWITCH` / `PROTECTED_FILE` / `SHELL_COMMAND`）在 v2 trust mode 下也产出 CONFIRM。已知 `SECURITY_USER_ALLOWLIST` 对应 v2 `CONTROL_PLANE` class，trust mode 下 matrix 是 CONFIRM ✓。其余需逐项验证 |
| `_check_trusted_path_skip` | 36 | v2 `_check_trusted_path`（read-only 等价已 wired）+ C8a `consume_session_trust` prune | 已具备 |

**结论**：3 个函数都可以删，但 agent.py 的 pre-LLM 入口（`_run_security_pre_check`-类调用方）必须先切到 v2 evaluate。这是 **C8b-4 的核心架构变更**，不是 inline 替换。

### §D — `mark_confirmed` 路径与 confirmed_cache 决策

`mark_confirmed` 当前职责（policy.py:1660-1690）：
1. 写 `_session_allowlist[session_id]`（用户选 "allow_session" 后该 session 内不再 confirm）
2. 写 `_confirmed_cache[(tool, params_hash)]`（同一 tool+params 的 retry 自动 allow）

C8b 必须三选一：

- **方案 A — v2 完整对等**：在 v2 加 `SessionAllowlistManager` + `ConfirmedCache`，对应 `mark_confirmed` 行为。优点：零行为变化。缺点：把 v1 的设计原样搬运到 v2，污染 v2 的"无状态决策引擎"原则。
- **方案 B — 完全删除**：UI confirm 后不再缓存。每次 retry 都重新走决策。优点：v2 干净。缺点：用户可能看到"我刚 allow 了为啥又问"——尤其 retry-on-error 场景。
- **方案 C — 仅保留 session_allowlist（推荐）**：v2 加 `SessionAllowlistManager`（可独立模块或 PolicyContext.session_grants 字段），承载 "allow_session" 后的 sticky 放行。retry-allow 缓存删除（reasoning_engine 的 retry 路径已经能自己识别"上次 allow 过的 tool_use_id"）。

**推荐方案 C**，理由：retry-allow 在 v2 架构下其实是 reasoning_engine 的事（同一 tool_use_id 不应再触发决策），不该是 PolicyEngine 的职责。

### §E — 测试打架成本

| 文件 | LOC | v1 import 数 | 处理代价 |
|---|---|---|---|
| `tests/unit/test_security.py` | 705 | 22 | **重写**：测的几乎全是 v1 PolicyEngine 行为，需对照 v2 测试拆分保留/删除 |
| `tests/unit/test_permission_refactor.py` | 224 | 20 | **大改**：测 permission shim 与 v1 engine 协作 |
| `tests/unit/test_trusted_paths.py` | 216 | 12 | **小改**：测 `trusted_paths.py` 模块本身（v1/v2 共用）|
| `tests/unit/test_remaining_qa_fixes.py` | 157 | 5 | **小改**：少量 v1 import |
| `tests/unit/test_chat_clear_runtime.py` | 34 | 2 | **小改**：cleanup_session facade 已通 |
| `tests/e2e/test_p0_regression.py` | 198 | 3 | **小改**：1-2 处 |
| `tests/integration/test_gateway.py` | 314 | 2 | **小改**：1-2 处 IM trust mode |

合计**约 70-100 个 test case** 需要 review/迁移。其中 ~30 个能通过 facade 不变，~50 个需要重写 to v2 调用，~20 个 v1-only 测试可直接删除。

### §F — 推荐 sub-task 拆分（5 个 commit，每个独立可 rollback）

#### **C8b-1 — v2 补能（preparation, no v1 deletion）**

- v2 `_check_user_allowlist` 实现 v1 `_check_allowlists` 等价（matching + persistent）
- v2 `_check_death_switch` 实现连续 deny → readonly_mode 计数 + 切换
- 新增 `policy_v2/user_allowlist.py`：`UserAllowlistManager`（add/remove/save_to_yaml/load_from_yaml）
- 新增 `policy_v2/skill_allowlist.py`：`SkillAllowlistManager`（add/remove/clear/check）
- v1 不动；C8b-3 ~ C8b-5 才有迁移目标
- **风险**：低（纯加 v2 代码，v1 路径不变）
- **LOC**：+400 v2 / 0 v1 / 测试 +200
- **预计**：1-1.5 天
- **commit 边界**：所有 v2 stub 全部填实；新增 manager 单测 100% 覆盖；v1 测试全绿

#### **C8b-2 — 配置常量与 SecurityConfig 子段读取迁移（low risk）**

- 新增 `policy_v2/defaults.py`：把 `_default_*_paths` / `_DEFAULT_BLOCKED_COMMANDS` 移过去
- `config.py` × 6 import 改 `from policy_v2.defaults import ...`
- `config.py` × 6 `reset_policy_engine` callsite 改 `reset_engine_v2`（保留 v1 reset 为兼容）
- `checkpoint.py:250` 改读 `get_config_v2().checkpoint`
- `audit_logger.py:113` 改读 `get_config_v2().self_protection`
- **风险**：低（纯重命名 + 移位）
- **LOC**：+150 v2 / -120 v1（policy.py 仍未删，但常量移出）
- **预计**：半天
- **commit 边界**：config.py / checkpoint.py / audit_logger.py 不再 import policy.py 内部符号

#### **C8b-3 — UI confirm facade 完成切换 + confirmed_cache 决策（medium risk）**

- 实施推荐方案 C：新增 `policy_v2/session_allowlist.py`：`SessionAllowlistManager`
- `cli/stream_renderer.py` / `config.py:1792` / `chat.py:401` / `gateway.py:4778,4842` / `telegram.py:700` / `feishu.py:1090` × 7 callsite 全部改成直接调 `get_ui_confirm_bus()` + `SessionAllowlistManager`
- `tool_executor.py:807-810` retry-confirm 逻辑改为：通过 `tool_use_id` 去重（不再用 confirmed_cache）
- `policy.py` 删除 6 个 facade 方法（`store_ui_pending` / `cleanup_session` / `resolve_ui_confirm` / `prepare_ui_confirm` / `cleanup_ui_confirm` / `wait_for_ui_resolution`）
- `policy.py` 删除 `mark_confirmed` / `_session_allowlist` / `_confirmed_cache`
- **风险**：中（涉及 5+ 个 IM 适配器；retry-allow 行为变化用户可能感知）
- **LOC**：+200 v2 / -150 v1
- **预计**：1 天
- **commit 边界**：policy.py 不再有 UI confirm 任何代码；所有 callsite 直连 bus

#### **C8b-4 — agent.py RiskGate 删除（high risk）**

- 删除 `_consume_risk_authorization` / `_check_trust_mode_skip` / `_check_trusted_path_skip` 三个函数（共 ~166 行）
- pre-LLM 闸门入口（agent.py 内调用这些函数的地方）改成调 `evaluate_via_v2(message_intent_event, ctx)`
- chat handler 在 user 确认 risky message 后写 `PolicyContext.replay_authorizations`（C7 已加字段）
- 必须新增 audit：5 个 v1 "敏感 target 仍 confirm" 在 v2 trust mode 下确实产出 CONFIRM
- **风险**：高（用户可见行为：trust mode 是否生效、replay 是否记忆、trusted path skip 是否一致）
- **LOC**：+150 v2 / -350 v1
- **预计**：1.5 天 + 1 天测试 / audit
- **commit 边界**：agent.py 不再 import policy.py；trust mode + replay + trusted path 三组场景测试全绿；audit D7 (RiskGate parity) 全 PASS

#### **C8b-5 — PolicyEngine class 删除 + policy.py 文件删除（cleanup commit）**

- `agent.py:2463` `clear_skill_allowlists` 调 v2 `SkillAllowlistManager`
- `skills.py` × 4 callsite 调 v2 `SkillAllowlistManager`
- `security_actions.py` × 4 callsite 调 v2 `UserAllowlistManager` / death_switch helper
- 删除 `core/policy.py` 整文件
- 删除/迁移 `tests/unit/test_security.py` v1-only cases
- **风险**：中-高（删除即不可逆，需所有 callsite 迁完才能跑）
- **LOC**：-1700 v1 / +50 callsite 调整
- **预计**：1 天
- **commit 边界**：`grep -r "core.policy" src/ tests/` 全部命中只有 v2/policy_v2 文件

**总预计**：5-6 天有效工作；分成 5 个 commit；每个 commit 都能独立 rollback；中间任何一个 commit 后 release 都不会引入 regression。

### §G — 不要做的事（教训提醒）

1. **不要做"先删 RiskGate 再删 PolicyEngine"** —— RiskGate `_check_trust_mode_skip` 还依赖 v1 `_is_trust_mode`；孤立删 RiskGate 后 PolicyEngine 反而更难删。必须按 1→5 顺序。
2. **不要做"v1 改成 thin wrapper 再删"** —— C9b 已经把 UI bus 部分薄壳化；其余 v1 代码（决策路径、user/skill allowlist）若再做一次 thin wrapper 等于打地鼠，浪费 1 个 commit 不带来任何价值。
3. **不要做"按文件删"** —— 比如先删 `tool_executor.py` 里 mark_confirmed 调用，会留下"删了 confirmed_cache 但 PolicyEngine 还在写"的孤立中间态。按**功能 group**（A/B/C/D/E/F/G）切，每个 commit 关闭一组。
4. **不要做"v2 stub 没填就删 v1"** —— C8b-1 必须先做完。否则 `_check_death_switch` / `_check_user_allowlist` v2 仍 return None，删 v1 = 直接关掉这两个安全护栏。
5. **不要在 C8b 期间做风格调整** —— ruff fix / 命名优化 全部往 C18 cleanup 推；C8b 的每个 commit 都应该 100% 是"删 v1 / 加 v2 等价"，让 reviewer/git bisect 一眼能看清。

### §H — 选择题：用户在 C8b-1 开工前要决定的 3 件事

1. **confirmed_cache 命运**：方案 A（v2 完整对等）/ B（删）/ **C（仅保留 session_allowlist，推荐）**
2. **`_frontend_mode` shim 命运**：保留为 v2 配置外的独立 UI 状态字段 / 折叠到 `ConfirmationMode` 枚举（"yolo" → "trust"，"normal" → "default"）
3. **5 个 commit 是否需要中间版本号**：每个 commit 独立 release / 5 个 commit 整体作为一个 minor version

### §I — 给 C8b 起跑前的 health check

C8b 开工前应先跑：
- `python scripts/c8_audit_d1_completeness.py` ~ `_d5_compat.py`：确认 C8a/C9 不变量都还在
- `python scripts/c9_audit.py`：确认 C9a/C9b 不变量都还在
- `pytest tests/unit/test_policy_v2_*.py tests/unit/test_security.py`：v2 + v1 都绿（容许 8 个 pre-existing failure）
- `ruff check src/openakita/core/`：基线干净

完成 C8b-1 后必须再补 audit：
- 新增 `scripts/c8b_audit_d7_riskgate_parity.py`：枚举 v1 RiskGate 5 个必 confirm target，断言 v2 在 trust mode 下也产出 CONFIRM
- 新增 `scripts/c8b_audit_d8_state_isolation.py`：断言 `SessionAllowlistManager` / `UserAllowlistManager` / `SkillAllowlistManager` 三个 manager 不互相 import 也不被 PolicyEngineV2 直接耦合（解耦要求）

---

## C8b-1 实施记录

> Phase: v2 补能（preparation, no v1 deletion）
> Outcome: ✅ Done · all 17 audits PASS · unit 2675 passed (+60) · 0 net new regressions
> 用户决策（§H 三选项）: Q1=方案 C / Q2=保留独立 / Q3=每 commit 独立

### 实施范围

按「C8b 粒度化执行计划 §F」的 C8b-1 切片实施：

1. **`policy_v2/user_allowlist.py`** — `UserAllowlistManager`（engine-scoped）
   - `match(tool, params)` 等价于 v1 `_check_persistent_allowlist`（命令双 fnmatch + tool name 完全匹配）
   - `add_entry` / `add_raw_entry` / `remove_entry` / `snapshot` 取代 v1 `_persist_allowlist_entry` / `get_user_allowlist` / `remove_allowlist_entry`
   - `save_to_yaml(path=None)` 取代 v1 `_save_user_allowlist`，分离 mutate 与 IO（测试 / dry-run / batch save 都受益）
   - `replace_config(ua)` 给 C18 hot-reload 留接口
   - `command_to_pattern(cmd)` 提到模块级（v1 是 `PolicyEngine` static method）

2. **`policy_v2/skill_allowlist.py`** — `SkillAllowlistManager`（**module 级 singleton**，仿 `UIConfirmBus`）
   - `add(skill_id, tools)` / `remove(skill_id)` / `clear()` / `is_allowed(tool)` 等价于 v1 `_skill_allowlists` 字段 + 4 方法
   - 新增 `granted_by(tool)` / `snapshot()` 给审计用
   - 不持久化（与 v1 一致）
   - `get_skill_allowlist_manager()` / `reset_skill_allowlist_manager()`

3. **`policy_v2/death_switch.py`** — `DeathSwitchTracker`（**module 级 singleton**）
   - `record_decision(action, tool_name, enabled, threshold, total_multiplier)` 取代 v1 `_on_deny` + `_on_allow` 计数逻辑
   - `is_readonly_mode()` / `reset()` 取代 v1 `readonly_mode` 属性 + `reset_readonly_mode`
   - `set_broadcast_hook(callable)` **解耦 v2→api 反向耦合**：v1 直接 `from openakita.api.routes.websocket import broadcast_event`；v2 用 hook 注入（启动时由 api/routes/websocket 调一次）
   - `_NON_RESETTING_READ_TOOLS = {read_file, list_directory, grep, glob}` —— 与 v1 `_on_allow` 行为对齐

4. **`PolicyEngineV2._check_user_allowlist`（step 9 实装）**
   - 先查 engine-scoped `UserAllowlistManager.match`
   - 再查 process-wide `get_skill_allowlist_manager().is_allowed`
   - 任一命中 → relax CONFIRM → ALLOW
   - **bypass 边界**已由 step 调用顺序保证：safety_immune (3) / owner_only (4) / channel_compat (5) / matrix DENY (6) 都在前面

5. **`PolicyEngineV2._check_death_switch`（step 10 实装）**
   - 配置 disabled → 跳过
   - tracker.is_readonly_mode() == False → 跳过
   - readonly + class ∈ READONLY_CLASSES → 跳过（read 工具不被 readonly 拦）
   - 否则 → DENY

6. **`PolicyEngineV2.evaluate_tool_call` 末尾计数 hook**
   - 决策落定后调 `tracker.record_decision`，把决策结果反馈给 tracker
   - **engine-level flag `count_in_death_switch`**（默认 True）：dry-run preview engine 置 False 跳过计数

7. **`global_engine.make_preview_engine(cfg=None)`** —— **C8b-1 中发现的 P1 bug 修复**
   - 用 deepcopy(get_config_v2()) 或显式 cfg 构造 fresh engine
   - 自动 `count_in_death_switch = False`
   - 复用模块级 `_explicit_lookup`（避免 preview 与生产分类器漂移）
   - 取代 `/api/config/security/preview` 直接拿 global engine 的危险用法（详见下方"深度复审发现的 P1 bug"）

8. **`tests/conftest.py` autouse fixture `_isolate_policy_v2_singletons`**
   - 每个 test 前后调 `reset_death_switch_tracker()` + `reset_skill_allowlist_manager()`
   - 不能用 `.reset()` / `.clear()`：`DeathSwitchTracker.reset()` 故意保留 `total_denials`（v1 parity），test 间会污染
   - fail-soft：模块未导入时静默 yield（policy_v2 不在所有 test 范围内）

### 深度复审发现的 P1 bug

**Bug**: `/api/config/security/preview` endpoint 在 C9a §4 引入时，"use current config" 分支直接拿 `engine = get_engine_v2()` 即**全局 engine**。C8b-1 给 engine 加了 `record_decision` 调用后：
- preview 默认 sample 含 `("write_file", "/etc/passwd")` / `("run_shell", "rm -rf /")` 等会 DENY 的样本
- 用户每次按 "策略预览" 按钮，全局 tracker 计数 +6（共 9 个 sample，约 6 个会 DENY）
- 用户连按 1 次预览按钮就可能让真实 agent 进 readonly mode

**严重性**：P1 用户可见。任何用户尝试预览策略效果就把自己的 agent 卡死。

**修复**：
- 新增 `make_preview_engine(cfg=None)` helper，强制 `count_in_death_switch=False` + `deepcopy(cfg)`
- preview endpoint 两条分支都改用 `make_preview_engine`（proposed 和 current 都构造 ad-hoc engine）
- 新增 D6 audit dimension（`scripts/c8b1_audit.py`）专门防漂——任何后续改动若让 preview 重新走 global engine 立即 audit 失败
- 新增 2 个回归测试（`TestC8b1PreviewIsolation`）

**为什么之前没发现**：C9a 时 step 10 还是 stub return None，preview engine DENY 也不计数。C8b-1 启用计数后才暴露。属于"两个独立改动叠加产生的隐藏 bug"——单独看每个 commit 都没问题。教训：**新增 cross-cutting 副作用（如 record_decision）后必须 grep 一下所有创建 engine 的地方**。

### 测试结果

- `pytest tests/unit/`: **2675 passed** / 8 failed / 4 skipped
- 8 失败 = baseline 同批 8 个 pre-existing failures（test_org_setup_tool / test_reasoning_engine_user_handoff / test_remaining_qa_fixes / test_wework_ws_adapter）
- **+60 new tests** 全部 passing（`tests/unit/test_policy_v2_c8b1_managers.py`）
- 17 audits 全 PASS：C8 D1-D5 + C9 D1-D6 + C8b-1 D1-D6
- ruff：所有改动文件 PASS

### 给 C8b-2 留的接口

- `make_preview_engine` 可被 C8b-2 配置常量迁移阶段沿用（不需要再做新的 preview adapter）
- conftest autouse fixture 已经覆盖 SkillAllowlist + DeathSwitch；C8b-3 引入 `SessionAllowlistManager` 时按同模式扩展
- `UserAllowlistManager.save_to_yaml(path)` 可让 C8b-5 删除 v1 `security_actions.add_security_allowlist_entry` 时无缝迁移

### 工程教训

1. **process-wide singleton 在 test 中是定时炸弹**：`DeathSwitchTracker` / `SkillAllowlistManager` 一旦没 autouse fixture 隔离，1 个测试的 deny 会让后续 16 个测试看到 readonly = StopIteration 链式爆炸。教训：新增任何 module singleton **同步加 conftest 隔离**，否则该 PR 必有"测试在我机器上能过"的诡异 race。

2. **`reset()` ≠ "干净 fixture"**：v1 parity 让 `reset()` 故意保留 `total_denials`，但 test 间隔离需要彻底清空。两种语义不能混用——production 用 `reset()`，test 用 `reset_*_tracker()` 重新构造 singleton。

3. **加 cross-cutting 副作用前先扫所有 caller**：把 `record_decision` 加到 `evaluate_tool_call` 末尾"看起来"是单一改动，但任何对 engine 的"借用调用"（如 preview）瞬间被波及。grep `evaluate_tool_call(` 是新增任何这种 hook 前的必做动作。

4. **架构边界要在代码里强制**：`death_switch.py` 用 broadcast hook 而不是直接 import api 模块——audit D2.4 检查"v2→api 反向耦合"会自动失败。这种边界靠"约定"维持迟早会破，靠 grep audit 才稳。

5. **`make_preview_engine` 模式可推广**：今后任何"我要在不污染全局状态的前提下评估"场景都应该走 ad-hoc engine + 显式 flag，不要 mutate global singleton 再恢复（race 隐患）。

C8b-1 完成，可进入 **C8b-2（配置常量与 SecurityConfig 子段读取迁移）**。

---

## C8b-2 实施记录

时间：C8b-1 之后下一步。
依据：「C8b 粒度化执行计划 §F · C8b-2 — 配置常量与 SecurityConfig 子段读取迁移（low risk）」。

### 0. Recon（最终 scope 收敛）

| 维度 | 状态 |
|---|---|
| `_default_protected_paths` / `_default_forbidden_paths` / `_default_controlled_paths` 在 v1 `policy.py` | 3 个 platform-specific 函数 |
| `_DEFAULT_BLOCKED_COMMANDS` 在 v1 `policy.py` | 1 个 list constant |
| `policy_v2/shell_risk.py` 已有 `DEFAULT_BLOCKED_COMMANDS` | 同等内容！意外发现的重复定义 → 必须合并到单一 SoT |
| `config.py` 私有符号 import | × 3 处（`_default_forbidden_paths` / `_default_protected_paths` / `_DEFAULT_BLOCKED_COMMANDS`） |
| `config.py` 调 `reset_policy_engine` | × 6 处（每个 SecurityConfig PATCH endpoint 一处） |
| `config.py` 调 `get_policy_engine` | × 4 处，留到 C8b-5 折叠 `_frontend_mode` shim 时一起处理 |
| `audit_logger.py:113` | 读 v1 `pe.config.self_protection.audit_path/audit_to_file` → 改读 v2 `cfg.audit.log_path/enabled`（v2 已拆出独立 `AuditConfig`，字段名不同需 inline） |
| `checkpoint.py:250` | 读 v1 `pe.config.checkpoint` → 改读 v2 `cfg.checkpoint`（同名同字段，零 rename） |
| `config.py:1888` self-protection CRUD endpoint | UI 仍读 v1 schema，是 SecurityView v1 部分 → **留到 C9c 一起重做**，本 commit 不动 |
| YAML migration | `policy_v2/migration.py:207-212` 已自动转换 `audit_to_file→enabled` / `audit_path→log_path`，旧 YAML 无需手改 |

### 1. 实施步骤

1. **新增 `core/policy_v2/defaults.py`**（5.6 KB）—— 4 个公开符号：
   - `default_protected_paths()` / `default_forbidden_paths()` / `default_controlled_paths()` —— 平台相关函数（每次返回 fresh list 防共享 mutate）
   - `default_blocked_commands()` + `DEFAULT_BLOCKED_COMMANDS` tuple —— **重导出自 `shell_risk`，单一 SoT**

2. **v1 `core/policy.py` 三个函数 + 一个 list 退化为 thin re-export**（135 行 → 27 行）：
   ```python
   from .policy_v2.defaults import default_protected_paths as _v2_default_protected_paths
   _DEFAULT_BLOCKED_COMMANDS: list[str] = list(_V2_DEFAULT_BLOCKED_COMMANDS)
   def _default_protected_paths() -> list[str]:
       return _v2_default_protected_paths()
   ```
   旧 caller（`tests/e2e/test_p0_regression.py` 等）继续工作，C8b-5 删 v1 时一起去除。

3. **新增 `core/policy_v2/global_engine.reset_policy_v2_layer()`** —— C8b-2 起 config.py 用此 helper 替代 v1 `reset_policy_engine`。语义：
   - `reset_engine_v2()` 清 v2 单例
   - `reset_audit_logger()` 清 audit_logger 单例（C8b-2 起 audit 改读 v2，必须一并失效）
   - fail-safe：audit_logger 模块未加载时静默 skip

4. **`config.py` × 8 处 callsite 迁移**：
   - 6 处 `reset_policy_engine()` → `reset_policy_v2_layer()`（自动 grep 替换全 6 处）
   - 1 处 `from openakita.core.policy import _default_forbidden_paths, _default_protected_paths` → `from openakita.core.policy_v2.defaults import default_forbidden_paths, default_protected_paths`
   - 1 处 `from openakita.core.policy import _DEFAULT_BLOCKED_COMMANDS` → `from openakita.core.policy_v2.defaults import default_blocked_commands`
   - permission-mode endpoint 单独处理：`get_policy_engine` 留（用于设 `_frontend_mode`）+ `reset_policy_engine` 改 `reset_policy_v2_layer`

5. **`audit_logger.get_audit_logger()` 改读 v2**：
   ```python
   from .policy_v2.global_engine import get_config_v2
   cfg = get_config_v2().audit
   _global_audit = AuditLogger(path=cfg.log_path or DEFAULT_AUDIT_PATH, enabled=cfg.enabled)
   ```
   inline rename `audit_path → log_path`、`audit_to_file → enabled`。

6. **`checkpoint.get_checkpoint_manager()` 改读 v2**：
   ```python
   from .policy_v2.global_engine import get_config_v2
   cfg = get_config_v2().checkpoint
   ```
   字段名同 v1，零 rename。

7. **`policy_v2/__init__.py` 导出**：4 个 default 函数 + `reset_policy_v2_layer`。

### 2. 测试

新增 `tests/unit/test_policy_v2_c8b2_defaults.py`（16 tests）：
- **TestDefaultsParityWithV1** × 5 —— 4 个 default 函数与 v1 私有 `_default_*` 完全等价；`DEFAULT_BLOCKED_COMMANDS` 与 `shell_risk` 单一 SoT
- **TestDefaultsListMutationSafety** × 4 —— 每次返回 fresh list（防 v1 `.append` 习惯污染）
- **TestSubsystemsReadV2Config** × 3 —— audit_logger / checkpoint 在"v2 已配置 + v1 PolicyEngine 未初始化"环境下能正确初始化
- **TestResetPolicyV2Layer** × 2 —— hot-reload 契约：v2 engine + audit_logger 都被清
- **TestConfigPyDoesNotImportV1Internals** × 2 —— 静态扫描 config.py 不再 import v1 私有 / 废弃符号

新增 `scripts/c8b2_audit.py`（6 dimensions）：
- D1 完整性、D2 单一 SoT、D3 v1 退化为 re-export、D4 子系统读 v2、D5 config.py 解耦、D6 reset 契约
→ **6 维全 PASS**。

### 3. 验证

```
$ python scripts/c8b2_audit.py
=== C8b-2 D1 completeness ===  ... OK
=== C8b-2 D2 single source of truth ===  ... OK
=== C8b-2 D3 v1 degraded to re-export ===  ... OK
=== C8b-2 D4 subsystems read v2 ===  ... OK
=== C8b-2 D5 config.py decoupled ===  ... OK
=== C8b-2 D6 reset_policy_v2_layer hot-reload ===  ... OK
C8b-2 ALL 6 DIMENSIONS PASS

$ pytest tests/unit/
2691 passed, 4 skipped, 8 failed (all pre-existing, identical to C8b-1 baseline)
# +16 = 16 new C8b-2 tests, 0 new regressions
```

**所有 23 维度 audit（C8 D1-D5 + C9 D1-D6 + C8b-1 D1-D6 + C8b-2 D1-D6）全 PASS。**

### 4. Bug fixes during implementation

无新发现 P1/P2 bug。Recon 阶段提前发现的潜在重复定义（`shell_risk.DEFAULT_BLOCKED_COMMANDS` vs. 新 `defaults.DEFAULT_BLOCKED_COMMANDS`）在落地前主动消解为单一 SoT，audit D2 强制不允许重新出现 list literal 重复。

### 5. 工程教训

1. **新增模块前先 grep 整个 package 是否已存在同语义符号**：本次发现 `shell_risk.DEFAULT_BLOCKED_COMMANDS` 与计划新增的常量同等内容，因此重导出而非重新定义，避免 v1→v2→v3 时的"3 处都要更新"陷阱。
2. **Hot-reload 契约要在 helper 函数里固化**：v1 `reset_policy_engine` 把"reset v2 + reset audit"两件事打包成一个 entry——C8b-2 起这个职责显式归到 v2 自己（`reset_policy_v2_layer`），避免删 v1 后 callsite 散落到处叫多个 reset。
3. **重命名不要做隐式映射**：`audit_to_file → enabled` 这种字段名变化，迁移层（`migration.py`）和读取层（`audit_logger.py`）必须同时改，否则会在 hot-reload 路径下产生静默 fallback 到默认值。本次保留了 v1 老 YAML 的自动迁移路径，新代码只读 v2 字段名。
4. **测试静态扫描 import 的价值**：`TestConfigPyDoesNotImportV1Internals` × 2 用文本断言守住"config.py 不再 import v1 私有符号"——这是普通 unit test 抓不到的"代码质量"维度，但又是删 v1 的硬前置。

C8b-2 完成，可进入 **C8b-3（UI confirm facade 完成切换 + confirmed_cache 决策）**：新增 `policy_v2/session_allowlist.py`；7 个 IM/CLI/web callsite 直连 `get_ui_confirm_bus()` + `SessionAllowlistManager`；`tool_executor.py:807-810` retry-confirm 改用 `tool_use_id` 去重；`policy.py` 删除 6 个 facade 方法 + `mark_confirmed` + `_session_allowlist` + `_confirmed_cache`。详见「C8b 粒度化执行计划 §F · C8b-3」。

---

## 附录 B：术语表

| 术语 | 含义 |
|---|---|
| ApprovalClass | 11 维工具语义分类，决策的核心维度 |
| confirmation_mode | 5 档：default / accept_edits / trust / strict / dont_ask |
| session_role | 4 档：plan / ask / agent / coordinator |
| safety_immune | 永远 ask 的精细路径白名单（identity/SOUL.md 等）|
| owner_only | 仅 owner 可调的工具集（IM 渠道额外卡死）|
| unattended_strategy | 计划任务/Webhook/spawn 的 4 种 confirm 处理策略 |
| RiskGate | pre-LLM 层的意图分类闸门（agent.py 内）|
| replay_authorization | 30s TTL 内复读消息免 confirm 的机制 |
| trusted_path_overrides | 用户 "allow_session" 后 session 内的路径白名单 |
| pending_approval | 计划任务被拦时的待审批记录 |
| DeferredApprovalRequired | unattended 任务遇 confirm 时抛的异常，让 task 暂停 |
| tool_intent_preview | 新增 SSE 事件，LLM 刚生成 tool_use 时的预览 |
| delegate_chain | 多 agent 嵌套时的调用链，confirm 冒泡到 root_user |
