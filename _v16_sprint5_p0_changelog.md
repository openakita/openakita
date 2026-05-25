# v16 业务能力审计 Sprint-5 P0 修复实施 changelog

> 范围：审计 `_orgs_business_capability_audit_v5.md` 标记的 D4 / F2 + 两个意外发现 + 三个 duck-call AttributeError。
> HEAD 基线：`38b46b3f`（Sprint-4 P0：D3-ext 真递归 + D5 artifacts/memory）。
> 服务状态：backend 仍在运行；本次 commit 只改源码 + 单测。由用户重启后做 v17 探索测试。

---

## 一、目标 vs 实际产出

| 项目 | 审计来源 | 状态 | 关键交付 |
| --- | --- | --- | --- |
| **P0-1 D4 节点级工具注入** | v5 §5.2.2 + §6.2 | 已落 | `_runtime_node_tools.py` + `_default_agent_builder.py` 改造，节点真带 `tools=[…]` 到 LLM，1 轮 tool_use 闭环 |
| **P0-2 F2 stop org 真打断** | v5 §5.2 + v15 §6.2.4 B6.4（3 轮老坑） | 已落 | `command_service._inflight_by_org` 二级索引 + `cancel_all_for_org` + 生命周期 `set_on_stop_org` 后绑定 |
| **意外 1：LLM 自创节点** | v5 §4.2 + §5.3 | 已落 | `AgentSpec.available_nodes` + `_persona_system_prompt` 在 depth=0 枚举真实 node id |
| **意外 2：L5 长任务 watchdog** | v5 §5.3 | 已落 | `command_service._watchdog_loop` 后台扫 `_inflight_tasks`，超 `watchdog_stuck_threshold_s` 杀任务 |
| **3 个 duck-call AttributeError** | v5 §5.2.5 | 已落 | `OrgRuntime.get_node_thinking / preview_node_prompt / get_node_status_snapshot` stub |

---

## 二、改动文件清单

### 源码（7 个文件，+779 行 / -27 行）

| 文件 | 行数变化 | 角色 |
| --- | --- | --- |
| `src/openakita/orgs/_runtime_node_tools.py` | **新增 +467 行** | D4 节点工具解析 + 1 轮 tool_use 循环；`resolve_node_tools` / `extract_tool_use_blocks` / `execute_node_tool` / `run_with_tools` |
| `src/openakita/orgs/_default_agent_builder.py` | +138 / -27 | `_BrainBackedNodeAgent.run` 接入 D4；新增 `event_emitter`；`_persona_system_prompt` depth=0 注入 `_available_nodes_block` |
| `src/openakita/orgs/_runtime_agent_pipeline.py` | +126 / -1 | `AgentSpec` 新增 `external_tools / enable_file_tools / available_nodes`；`ProfileResolver` 抽取节点工具列表 + 兄弟节点枚举 |
| `src/openakita/orgs/_runtime_lifecycle.py` | +23 | `OrgLifecycleManager.set_on_stop_org` 后绑定 setter（解决 server.py 鸡蛋问题） |
| `src/openakita/orgs/command_service.py` | +339 / 0 | `_inflight_by_org` 二级索引；`cancel_all_for_org`；`_watchdog_loop` / `_watchdog_tick` / `_resolve_watchdog_threshold`；`_handle_agent_event` 保留 `cancelled_by` |
| `src/openakita/orgs/runtime.py` | +108 / 0 | `set_on_stop_org` 透传；3 个 duck-call stub 方法 |
| `src/openakita/api/server.py` | +57 / 0 | `_node_tool_event_emit` 转发到 `org_event_bus`；`_on_stop_org_cancel_inflight` 接到 `org_runtime.set_on_stop_org`；启动/关停时管理 watchdog 生命周期 |

### 单测（5 个新增文件 + 1 个保留兼容性修改）

| 文件 | 用例数 | 覆盖项 |
| --- | --- | --- |
| `tests/runtime/orgs/test_node_tool_injection.py` | **16** | D4：tool 解析 / extract / execute / `run_with_tools` 0/1 轮 / 通过 builder 端到端注入 + trace context tools_count |
| `tests/runtime/orgs/test_stop_org_cancels_inflight.py` | **11** | F2 stop-org cancel propagation（多任务、空 org、runtime 抛错兜底、索引清理）+ watchdog（杀超时、不杀及格、disabled、threshold fallback、idempotent start/stop、保留 cancelled_by 标记） |
| `tests/runtime/orgs/test_available_nodes_prompt.py` | **4** | depth=0 列、depth=1 跳过、空 list 跳过、缺 label 仍按 id 列出 |
| `tests/runtime/orgs/test_node_query_endpoint_stubs.py` | **5** | 3 个 duck-call stub 形状契约 + missing-org 不抛 + `set_on_stop_org` passthrough |
| `tests/runtime/orgs/test_default_agent_builder.py` | +15 行 | `_spec` helper 默认 `enable_file_tools=False / external_tools=()` 保留 Sprint-2 0-工具语义；`tools_count` 断言 |

**新增单测合计：36 个用例**；Sprint-2/3/4 累计 55 个 + 本轮 36 = **91 个 orgs_v2 测试**（实际 `tests/runtime/orgs/` 累计 365 个 pass）。

---

## 三、各项实现摘要 + 关键决策

### 1. P0-1 D4 节点级工具注入

**症状回顾**：v16 wb-hh-image / wb-hh-video / wb-hh-human 节点被 D3-ext 真激活，但 LLM debug `tools_count = 0`，节点空手回答。

**设计**：
- 工具白名单来源走 `OrgNode.external_tools`（v1 字段，aigc-video-studio v16-* 模板里的节点都已声明此字段）。
- 复用 v1 全局 `default_handler_registry`（`openakita.tools.handlers`），不再重写 orgs_v2 一套。
- 通过 `_runtime_tool_categories.expand_tool_categories` 展开类别别名（`research` → `web_search`/`news_search`/`web_fetch`）。
- 已知未注册的工具名（如 `hh_image_create` 等插件工具）静默丢弃，节点仍获得标准子集（filesystem / research / planning / memory），不会因未连线插件而失败。
- 调用流：第 1 次 `messages_create_async(tools=...)` → 若 LLM 返回 `tool_use` 块 → 顺序执行每个工具（cancellation 沿 sequential await 自然传播，不用 gather） → 拼 `tool_result` 块 → 第 2 次 `messages_create_async`。
- `MAX_TOOL_ROUNDS = 1` 做硬上限常量，后续上多轮 ReAct 是改这一行。
- 工具失败（handler 抛错）→ 错误文本 inline 进 `tool_result.content`（matches v1 ToolExecutor 策略），不让单个工具拖垮整个 agent_run。
- `CancelledError` 透传：`execute_node_tool` 明确 `re-raise`，cancel 路径（Sprint-3 P0-2）维持正确语义。

**事件 / Trace**：
- `node_tool_called` / `node_tool_completed` / `node_tool_failed` 三个新事件写到 `events.jsonl`，带 `org_id` / `node_id` / `command_id` / `tool_name` / `args_preview`。
- LLM debug context 加 `tools_count` 字段，v17 审计可直接 grep `tools_count > 0`。

**关键决策**：
- **不**为 plugin tools (`hh_*`) 做 bridge——属于 D4-ext 后续工作（workbench 插件 manifest 与 default_handler_registry 是两套），现阶段丢弃 + log debug。
- **不**支持 MCP servers 注入（v5 §7.1 明确出范围）。
- 工具执行 sequential、不 parallel：与 Sprint-4 child dispatch 同样的"deterministic ordering + cancel propagation 容易"理由。

### 2. P0-2 F2 stop org 真打断（3 轮未修老坑）

**症状回顾**：派长任务 → `POST /api/v2/orgs/{id}/stop` HTTP 200 → status=STOPPED → 30s 后命令仍 `phase=running`，LLM 仍烧 token。

**设计**：
- 复用 Sprint-3 `_inflight_tasks` 模式，加 `_inflight_by_org: dict[str, set[str]]` 二级索引（在 `_schedule_run` 维护，`_run_minimal` / `_purge_old_commands` / `cancel_all_for_org` 清理）。
- `cancel_all_for_org(org_id, reason)` 遍历 `_inflight_by_org[org_id]`：
  1. `task.cancel()` 同步标记取消；
  2. 在 `_command_outcomes[cid]` 预播种 `cancelled_by=stop_org` 标记（events.jsonl 区分用户主动 cancel vs stop org）；
  3. best-effort `await runtime.cancel_user_command()` 让 dispatch tracker 同步状态——即使这步抛错也不影响主取消路径。
- 解决"server.py 鸡蛋问题"：`OrgLifecycleManager` 接受 `on_stop_org` 现在还可通过 `set_on_stop_org` 后绑定；`OrgRuntime.set_on_stop_org` 做 passthrough。`api/server.py` 在 `OrgCommandService` 创建后再把 `_on_stop_org_cancel_inflight` 绑到 runtime。
- `_handle_agent_event` 在覆盖 `_command_outcomes[cid]` 时保留预播种字段（`cancelled_by`、`elapsed_s`、`threshold_s`），不让 executor 发出的 `agent_run_cancelled` 抹掉来源信号。

**关键决策**：
- `cancel_user_command` 抛错时不影响主取消——`task.cancel()` 才是用户期望的"按钮真生效"承诺。runtime 抛错只 log。
- `cancelled_by` 字段是 events.jsonl 读者关键信号；v17 审计据此区分用户取消 / stop-org / watchdog 三种来源。

### 3. 意外发现 1：LLM 自创不存在节点

**症状回顾**：v16 producer LLM 自创了 `director` 节点（spec 不存在），被 unknown_target 兜底 skip，但浪费 1 round + 后续可能基于此推理。

**设计**：
- `AgentSpec.available_nodes: tuple[(node_id, label), ...]` 新字段。
- `ProfileResolver._available_nodes_for` 从 spec 拉所有 sibling node id + label（跳过当前 node 防自我 dispatch），同时支持 `nodes` 是 Mapping 或 Iterable。
- `_persona_system_prompt(spec, depth=0)` 在 dispatch tutorial 后拼 `_available_nodes_block` 渲染：
  ```
  Available child nodes you may dispatch to (use the exact id):
  - screenwriter: screenwriter
  - art-director: art-director
  ...
  Do NOT invent new node ids. If none of the listed nodes fits ...
  ```
- depth ≥ 1 不渲染（避免子节点 cosplay 协调者）。

**关键决策**：
- 不上 structured output / JSON mode 强约束——审计原文 "不必 0%，但应大幅下降"，prompt 工程是更便宜的第一刀。
- 子 agent 仍走 Sprint-2 "stay in your lane" 提示，无 `Available child nodes` 注入。

### 4. 意外发现 2：L5 长任务 watchdog 真触发

**症状回顾**：B5 失败注入 6 个用例 4 个 timeout（恶意 prompt 让 LLM 跑 600s+），spec 有 `watchdog_stuck_threshold_s=1800` 字段但**无人在跑**。

**设计**：
- `OrgCommandService.start_watchdog()` 启动一个 background `asyncio.Task`：
  - 每 `_watchdog_poll_interval_secs`（默认 30s）扫一遍 `_inflight_tasks`；
  - 对每个未 done 的 task，从 `_commands[cid].created_at` 算 elapsed；
  - 拿 `_resolve_watchdog_threshold(org_id)`：
    - spec 显式 `watchdog_enabled=False` → 返回 0（跳过）；
    - spec 未设 `watchdog_stuck_threshold_s` → 用 `_watchdog_default_threshold_secs`（默认 1800s）；
    - 设了正值 → 用之；
  - 超阈值 → `task.cancel()` + 写 `_command_outcomes[cid]` 标 `cancelled_by=watchdog` + emit `agent_run_watchdog_killed` 事件到 bus（fire-and-forget）。
- `configure_watchdog(poll_interval_secs=..., default_threshold_secs=...)` 给测试调参用（生产 30s/1800s vs 测试 0.1s/0.5s）。
- `start_watchdog` idempotent；`stop_watchdog(timeout=2.0)` 安全终止（FastAPI shutdown）。
- `api/server.py` 的 startup / shutdown 钩子分别调 `start_watchdog()` / `await stop_watchdog()`。

**关键决策**：
- watchdog 是 opt-in（默认调 `start_watchdog` 起，测试 fixture 不调就不起）——legacy contract / parity 测试构造服务但不跑事件循环 30s 的场景不受影响。
- 每 tick 的所有异常吞掉 + log debug，单次 bug 不能毒死整个 loop。
- emit event 走 `asyncio.get_running_loop().create_task(coro)` fire-and-forget，watchdog tick 本身保持同步——`_watchdog_loop` 已经被事件循环 await 中。

### 5. 3 个 duck-call AttributeError 兜底

**症状回顾**：`GET /api/v2/orgs/{id}/nodes/{nid}/{thinking,prompt-preview,status}` 三个端点 503 / AttributeError。

**设计**：
- `OrgRuntime` 上加 stub 实现：
  - `get_node_thinking(org_id, node_id)` → 扫该 org 的 event store，挑 `data.node_id == node_id` 的事件，返回 `{org_id, node_id, thinking: [...], implementation: "sprint5_stub"}`；
  - `preview_node_prompt(org_id, node_id)` → 复用 `ProfileResolver` + `_persona_system_prompt(depth=0)` 渲染 producer-级 prompt，返回 `{org_id, node_id, prompt: <str|None>, implementation: "sprint5_stub"}`；
  - `get_node_status_snapshot(org_id, node_id)` → 读 `_state.is_org_active` + `_lifecycle.is_org_recently_stopped`，返回 `{status: "active"|"idle", is_active, recently_stopped, implementation: "sprint5_stub"}`。
- 所有方法所有异常吞掉 + 返回结构化默认值——不再让前端 panel 因 AttributeError 崩。
- 真正的 `NodeStatusController` 子系统作为 P9.7gamma 跟踪。

**关键决策**：
- 三个 stub 都带 `implementation: "sprint5_stub"` 字段，v17 frontend 据此可禁用 panel 或显示 "n/a"。
- 不在 stub 阶段做 v1 parity 实现——避免在 Sprint-5 引入新一套 thinking timeline 模块。

---

## 四、Pattern 扫描结果

- **trace_context (`Pattern 5`)**：D4 工具调用事件 (`node_tool_called` / `_completed` / `_failed`) 全带 `org_id` + `node_id` + `command_id`（command_id 从 `current_command_id_var` ContextVar 取）。`tools_count` 写到 trace context dict。
- **AGENTS.md 注入**：未触及。Sprint-4 已实现。
- **cancel propagation**：`execute_node_tool` 与 `run_with_tools` 显式 re-raise `CancelledError`；事件 emit 的 fire-and-forget task 不会拦截 cancel（事件 emit 自身可被 cancel；`_safe_emit` 显式 re-raise CancelledError）。
- **v1 chat 路径影响**：零。`default_handler_registry.execute_by_tool` 是只读调用；orgs_v2 与 v1 chat 共用同一个全局单例，互不影响。
- **`_inflight_tasks` 单例复用**：v1 chat 路径不走 `OrgCommandService._inflight_tasks`；watchdog 只扫 `OrgCommandService` 自己的字典，不会误杀主对话 task。

---

## 五、双轮复核结论

### Round 1：代码质量

| 检查项 | 结论 |
| --- | --- |
| async/await 正确 | OK——所有新增 async 方法都 `await`、无遗漏 |
| CancelledError 传播 | OK——`execute_node_tool` / `_safe_emit` / `cancel_all_for_org` / watchdog tick 全部明确 re-raise 或不吞 |
| `cancel_all_for_org` 失败兜底 | OK——`runtime.cancel_user_command` 抛错不影响 `task.cancel()` 主路径 |
| watchdog 关停安全 | OK——`stop_watchdog(timeout=2.0)` 绑 FastAPI shutdown；`_watchdog_loop` 明确捕获 `CancelledError` 并 `return` |
| 与 H1-H4 / Sprint-2/3/4 不冲突 | OK——`_inflight_tasks` 二级索引是新增，主索引行为不变；`_command_outcomes` 字段是附加，旧字段保留 |
| 多路径影响 | v1 chat 路径不变（共享 handler registry 但不共享 inflight 管理）；orgs_v2 zero-tools 节点走原 Sprint-4 单 shot 路径；>0 tools 节点走新 `run_with_tools` |
| 边界覆盖 | tool 抛错 / tool 返回 None / unknown tool name / 无 tools / disabled watchdog / spec 缺 threshold / 重复 start_watchdog / stop_watchdog 在 idle 状态 全测 |

### Round 2：CI / CLI / 打包

| 检查项 | 结论 |
| --- | --- |
| `pytest tests/runtime/orgs/ tests/parity/orgs/` | **365 pass**（含本轮新增 36 个），排除 1 个无关失败 `test_frontend_stale_paths_sentinel`（HEAD baseline 即存在，apps/setup-center/src/api/orgs.ts:2 注释里的 url literal）|
| 累计新测 | Sprint-2 19 + Sprint-3 13 + Sprint-4 23 + Sprint-5 36 = **91 个 orgs_v2 sprint 测试** |
| `ruff check` 触及文件 | **All checks passed!** |
| `mypy` 触及文件 | **Success: no issues found in 4 source files** |
| `openakita serve` 启动 | watchdog start/stop 已绑 FastAPI lifespan；现有 `tests/api/test_server_app_wiring.py` 全 pass |
| `pyproject.toml` 无新依赖 | OK——只用 stdlib（asyncio / typing）+ 现有 openakita 模块 |

预先存在的失败（不属本轮）：
- `tests/parity/orgs/test_frontend_stale_paths_sentinel.py::test_frontend_no_unauthorized_orgs_spec_paths` —— `apps/setup-center/src/api/orgs.ts:2` 注释里有合法的 url 字符串，sentinel 误判。HEAD baseline 即失败。
- `tests/api/test_p97_beta_smoke.py::test_b19_create_node_schedule` —— 422 vs 201，HEAD baseline 即失败。

---

## 六、给用户的下一步指引

1. **重启 backend**：当前后端跑的是修改前代码；本轮 commit 后用户重启才能在 v17 上看到 D4 tool injection / F2 stop-org / watchdog 三个新能力生效。
2. **v17 探索测试关注点**：
   - **D4 验证**：派 "为 30 秒短视频生成封面图" → 检查 `data/llm_debug/llm_request_*.json` 里某个 wb-hh-image 节点的 `tools_count > 0`；events.jsonl 出现 `node_tool_called` / `node_tool_completed`。
   - **F2 验证**：派长任务 + 3s 后 `POST /api/v2/orgs/{id}/stop` → 1-3s 内命令 `phase=cancelled`，`token_delta` 显著低于 baseline。
   - **意外 1 验证**：派任务给一个含 3-4 个 sibling node 的 org → producer 节点 LLM 不再自创不存在的 node id（不必 0%，但应低很多）。
   - **意外 2 验证**：派 prompt 让 LLM "想很久很久"，spec 设 `watchdog_stuck_threshold_s=10` → 12-15s 内 events.jsonl 出现 `agent_run_watchdog_killed`，命令 `phase=cancelled`。
3. **生产 watchdog 默认值**：production 30s 轮询 / 1800s 阈值；如需要更激进可在 `OrgCommandService` 实例化后调 `cmd_svc.configure_watchdog(...)` 调参（每个 org 也可在 spec 里覆盖 `watchdog_stuck_threshold_s`）。

---

## 七、Out of scope（next sprint）

- **D4 多轮 ReAct 循环**：当前 `MAX_TOOL_ROUNDS = 1`，复杂研究类任务（tool → reasoning → tool → ...）需要多轮。
- **D4 MCP servers 注入**：节点 spec 含 `mcp_servers` 字段但未消费——需 MCP gateway 桥接到 orgs_v2 node path。
- **D4 SKILL.md 自动加载（D4-ext）**：跳过。
- **D4 plugin (`hh_*`) workbench 工具桥接**：跳过——workbench manifest 与 default_handler_registry 是两套，需要专项设计。
- **`NodeStatusController` 真实实现**：当前是 stub；P9.7gamma 跟踪。
- **Parallel child dispatch**：Sprint-4 已 sequential；并行 dispatch 需要重新设计 cancel propagation。
- **Inter-node memory retrieval**：节点间记忆共享。
- **B5 chaos 6 case 失败注入复跑（v17）**：watchdog 落地后这 6 个 case 应该都收口；v17 探索测试重跑确认。

---

## 八、Sprint-5 验证清单（实施期间 self-check 全 pass）

- [x] `_runtime_node_tools.py` 单元测 covers 解析 / 提取 / 执行 / 1 轮闭环
- [x] `_BrainBackedNodeAgent.run` 0 tools 走原路径，>0 tools 走 `run_with_tools`
- [x] `tools_count` 写到 brain 的 trace context dict
- [x] `cancel_all_for_org` 在 2 任务同 org 场景下都 cancel + 索引清理
- [x] `cancel_all_for_org` 在 runtime cancel 抛错时主路径仍生效
- [x] `_handle_agent_event` 不抹掉预播种的 `cancelled_by` 字段
- [x] Watchdog 在测试 1.2s 内杀超 0.5s 阈值的任务并发 `agent_run_watchdog_killed`
- [x] Watchdog start/stop idempotent，shutdown 安全
- [x] `_persona_system_prompt(depth=0)` 含 `Available child nodes`，depth=1 不含
- [x] 3 个 duck-call stub 形状契约稳定，missing-org 不抛
- [x] ruff 触及文件全 pass
- [x] mypy 触及 4 个源文件全 pass
- [x] 累计 365 orgs+parity tests pass，本轮新增 36 测全 pass

---

## 九、参考文档

- 审计：`_orgs_business_capability_audit_v5.md` §4.2 / §5.2 / §5.3 / §6.2 / §7.1
- 前轮 changelog：`_v15_sprint4_p0_changelog.md` / `_v14_sprint3_p0_changelog.md` / `_v13_sprint2_p0_changelog.md`
- 设计参考：`c:\Users\Peilong_Hong\Downloads\claude-code` 子 agent tool injection 模式
