# Sprint-2 P0 实施 changelog（v13 业务能力审计 v2 修复）

> 本文档汇总 v13 审计 v2 报告 P0 阻塞的修复实施记录，作为 commit body 的内部依据。
> 实施时间：2026-05-23 (PowerShell / Windows)
> HEAD 基线：`9b17b15c`（H1+H2+H3+H4 已 wire），但业务核心被 `_NullAgentBuilder` 占位件阻塞。

---

## 1. 修改文件清单

### 新增源码 / 测试
| 路径 | 行数 | 用途 |
|------|------|------|
| `src/openakita/orgs/_default_agent_builder.py` | ~210 | `DefaultAgentBuilder` + `_BrainBackedNodeAgent` + `BuilderUnavailable` |
| `tests/runtime/orgs/test_default_agent_builder.py` | ~210 | P0-1 回归 11 用例 |
| `tests/runtime/orgs/test_command_status_reconciliation.py` | ~245 | P0-2 回归 8 用例 |

### 修改的现有源码
| 路径 | 改动 |
|------|------|
| `src/openakita/orgs/__init__.py` | 导出 `DefaultAgentBuilder` / `BuilderUnavailable` |
| `src/openakita/orgs/command_service.py` | `__init__(event_bus=...)` + `_handle_agent_event` + `_wire_event_bus` + `_run_minimal` 状态调谐 + `get_status` event_ref/error overlay + `_purge_old_commands` 同步清理 outcomes |
| `src/openakita/api/server.py` | 注入 `DefaultAgentBuilder(brain_provider=lazy lambda)` 替换默认 `_NullAgentBuilder`；`OrgCommandService(..., event_bus=org_event_bus)` |

---

## 2. P0-1 实施摘要

### 关键决策
1. **复用主对话 Brain，不重建堆栈**：节点 agent 直接调用 `Brain.messages_create_async` 单次，构造 persona/role 派生的 system prompt + 单条 user message + zero tools。这是审计明确允许的"最小 viable binding"路径。
2. **lazy brain provider**：lifespan 顺序导致 `create_app` 时 `app.state.agent` 还是 None；用闭包延迟读取，第一次 `build()` 调用时才解析。
3. **fail-fast with `BuilderUnavailable`**：当 brain provider 返回 None（启动竞态）或 brain 缺 `messages_create_async`（未来 LLM frontend 形态）时抛 `BuilderUnavailable`（继承 `RuntimeError`），executor 捕获后照旧 emit `agent_run_failed reason=agent_build_failed`——和老 `_NullAgentBuilder` 行为字节对齐，下游契约不破。
4. **保留 `_NullAgentBuilder`** 作为 `AgentCache` 的 fallback default（测试 fixture / 显式 zero-LLM 单元测试用），不再是生产默认。

### 出口验证
- `tests/runtime/orgs/test_default_agent_builder.py` 11/11 pass
- `set_trace_context({"caller": "orgs_v2_node_agent", ...})` 标记节点身份，为 v14 探索测试"orgs_v2 路径有 LLM 调用"提供 grep 锚点

### 明确 out-of-scope（next sprint 标 TODO）
- 多节点 dispatch / aggregator / delegation_logs（D3 / D4 / D5）
- 节点级 tool / skill / MCP 注入（节点目前 zero tools）
- 持久化身份 SOUL.md / AGENT.md / USER.md 分层（节点单 system prompt 是 < 500 字符的简化版本）
- prompt-budget / context-window 管理

---

## 3. P0-2 实施摘要

### 关键决策
1. **方案 A（事件订阅 in-memory index）**：在 `OrgCommandService.__init__` 接收 `event_bus`，订阅 `agent_run_started/finished/failed` 三个事件，维护 `_command_outcomes: dict[command_id, outcome_dict]`。
2. **`_run_minimal` 终态调谐**：`runtime.send_command` 总是返回 `"submitted"`（agent 失败信号只走 event bus），所以 finaliser 必须查 outcomes 决定 `status=done` 还是 `status=error`。
3. **`get_status` overlay**：在快照里增加 `event_ref` 字段；当 outcomes 显示 failed 而 cmd 还没翻 → 立即把 error 反射到响应（处理 race window）。
4. **named subscribe 而非 wildcard tap**：用 `EventBusProtocol.subscribe`（标准接口），不依赖 `add_tap`（`_InMemoryEventBus` 私有扩展）。便于未来注入 WebSocket / SQLite-backed bus。
5. **TTL 同步清理**：在 `_purge_old_commands` 一并 pop 同 cid 的 outcomes，防止长寿进程内存堆积。
6. **back-compat**：`event_bus` 是 keyword-only optional；既有 P9.4 contract / parity 测试都不传 → 服务照常 init，只是不调谐（行为退化到老路径）。

### Handler 同步性 + 顺序保证
- handler 注册为 sync `def`；`_InMemoryEventBus.emit` 对 sync handler 不需要 await
- 命名订阅者先于 wildcard tap 触发（已通过运行时验证：`['named', 'tap:agent_run_failed']`）
- `agent_dispatch` 在 `dispatch.send_command` 内 `await`；`emit` 也 `await`；所以 `runtime.send_command` 返回时所有 emit 已完成 → outcomes 已落定

### 出口验证
- `tests/runtime/orgs/test_command_status_reconciliation.py` 8/8 pass
  - 含 "agent_run_failed → status=error" / "agent_run_finished → status=done" / "live overlay during running window" 三个核心用例
- `tests/runtime/orgs/test_command_service_contract.py` 16/16 pass（back-compat 验证）

---

## 4. Pattern 1-4 扫描结果

| Pattern | 描述 | 扫描结果 | 处理 |
|---------|------|---------|------|
| 1 占位件假装能用 | `_Null*Builder` / `class.*Stub` / `raise NotImplementedError` | 仅 `_NullAgentBuilder` | 已替换为非默认（保留作 fallback） |
| 2 status / event 脱节 | `phase\s*=\s*['"]done` 等手写完成态 | 仅 `command_service._run_minimal` | 已修复（P0-2） |
| 3 Optional 字段无默认 | orgs_v2 schemas | 全部有默认（`commands.py` / `nodes.py` / `orgs.py` / `projects.py`） | 无需修改 |
| 4 duck-call 不存在方法 | `app.state.org_runtime\.` 直接调用 | 路由统一用 `_call_runtime_method` / `getattr(rt, ...)` 兜底 | 无需修改 |

---

## 5. 复核结论

### Round 1：代码质量 / 冲突 / 并发
- ✅ 加入代码无 bug：types 一致、无 None 解引、async/await 完整
- ✅ 不与 H1-H4 / Fix-G* 冲突：handler 在 named-subscriber 通道，run 在 H4 taps 之前；不修改 H3 注入路径
- ✅ 多路径不互相影响：v1 chat 走 `chat.py` → `agent.run`；orgs_v2 走 `executor.activate_and_run` → `_BrainBackedNodeAgent.run`。共享 Brain 实例但通过 `set_trace_context` 区分（best-effort 标记，文档化为已知限制）
- ✅ 边界覆盖：empty content（短路 noop）、超长 content（直接传给 brain，由 brain 端 budget 处理）、unicode（无任何字节级处理）
- ✅ 占位件清理一致：`_NullAgentBuilder` 仍可注入但默认改为 `DefaultAgentBuilder`
- ✅ 并发：`asyncio.Lock` + sync handler；handler 写 dict 是单赋值原子操作

### Round 2：CI / CLI / 打包
- ✅ pytest：tests/runtime/orgs/ + tests/api/ + tests/parity/orgs/ → **666 passed, 3 xfailed, 2 pre-existing failures**
  - 2 个失败已验证为 HEAD baseline 既有问题（test_b19_create_node_schedule 422、frontend stale paths sentinel 评论行误命中），与本次改动无关
  - 19 个新测试全部 pass
- ✅ ruff check：0 errors（6 个改动文件）
- ✅ mypy：0 issues（2 个新增源文件）
- ✅ CLI smoke：`from openakita.api.server import create_app; create_app(agent=None)` 不崩
- ✅ 前端 / Tauri / Capacitor / pyproject 均无改动

---

## 6. commit hash

待 commit 后填入。

---

## 7. 给用户的下一步指引

1. **重启 backend**（本次 commit 只改源码，不要求服务验证；用户拿新代码后重启即可）
2. **重跑 v13 探索测试**或新建 v14 探索测试，验证：
   - 单节点最简单输入 "你好" → 应得到真实 LLM 文字（非 `agent_run_failed`）
   - `events.jsonl` 应出现 `agent_run_finished` 事件
   - `GET /api/v2/orgs/{id}/commands/{cid}` 在节点失败时应 `phase=error, error=<reason>, event_ref=agent_run_failed`
   - LLM debug 文件应出现 `caller=orgs_v2_node_agent` 标记（grep 锚点）

如发现 brain provider 在某条命令时返回 None（启动竞态），等待 Agent 初始化完成再发命令；表现为 `agent_run_failed reason=agent_build_failed error=brain_provider_returned_none` 的行为是预期降级。
