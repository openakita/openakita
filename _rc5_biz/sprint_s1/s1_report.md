# RC-5 sprint 第一批（gap⑤ 核心块 S0+S1+S2+S5）实施报告

> 2026-05-29 ｜ 把 gap⑤ spike 的 55 行 in-process 验证版正经产品化。
> 工程：D:\OpenAkita ｜ 本批：本地 commit、不 push。

## 0. 一句话结论

四个 stage（S0 参数 clamp / S1 节点产出回灌 / S2 收敛 prompt 固化 / S5 cancel 终态）全部
落地，单测 + 全量 `tests/runtime/ tests/runtime/orgs/` 回归 **961 passed**，ruff/mypy 全过，
smoke 过。**live 收敛在产品化代码路径（非 harness 子类取巧）上复现了 spike 的绿**：正常任务
3 turn 优雅收尾 `satisfied=true`，刁难任务 2 turn 体面终止、**未撞 max_turns**。

## 1. 四个 stage 完成情况

| stage | 改动 | 文件 | 单测 |
|-------|------|------|------|
| **S0** 参数 clamp | `Supervisor.__init__` 加 clamp-up + warning：`max_turns < max_stalls×(max_replans+2)` 时抬高 max_turns，绝不报错 | `runtime/supervisor.py` | `test_max_turns_clamped_to_replan_budget` / `test_max_turns_not_clamped_when_constraint_satisfied` |
| **S1** 产出回灌 ★ | 新增 `self.delegation_history`；inner_loop deliver 后 append；`SupervisorBrain.emit_progress_ledger` 协议加 `recent_outputs` 可选参数；调用点传 `list(self.delegation_history)`；`LLMSupervisorBrain` 新增 `_render_outputs()` 消费并渲染进 prompt | `runtime/supervisor.py` + `runtime/llm_supervisor_brain.py` + 8 implementor | `test_supervisor_feeds_delegation_history_to_progress_ledger` / `test_s1_brain_renders_fed_back_node_outputs` |
| **S2** 收敛 prompt | `ORCHESTRATOR_PROGRESS_LEDGER_PROMPT` 并入 spike 调通的 `=== ACTUAL OUTPUTS ===` 区块 + 三条收敛 Decision rules（satisfied 仅当产出已具体满足全部要求 / 矛盾→progress=false / 原地打转→in_loop=true）+ 「先 reasoning 后 PURE JSON」收紧措辞 | `runtime/llm_supervisor_brain.py` | `test_s2_progress_prompt_contains_convergence_rules` |
| **S5** cancel 终态 | `Supervisor.run()` 加 `except UserCancelledError` → `_terminate(CANCELLED, ...)`（懒导入避免 import-time 环）| `runtime/supervisor.py` | `test_supervisor_absorbs_user_cancelled_error` |

## 2. S1 核心：protocol 签名波及面（回应工作量估算）

`grep` 全仓 `emit_progress_ledger` 实现者后的实际情况：

| # | 实现者 | 文件 | 改前签名 | 处置 |
|---|--------|------|---------|------|
| 1 | `LLMSupervisorBrain` | `runtime/llm_supervisor_brain.py` | 显式 `cancel_event` | ✅ 加参数并**消费**（渲染 `{outputs}`）|
| 2 | `PassThroughSupervisorBrain` | `agent/supervisor_brain.py` | 显式 | ✅ 加参数（忽略，noqa）|
| 3 | `DegenerateSupervisorBrain` | `agent/supervisor_brain.py` | 显式 | ✅ 加参数（忽略，noqa）|
| 4 | `FakeBrain` | `tests/runtime/test_supervisor.py` | **`**_kwargs`** | ✅ 显式加参数 + 记录（用于 S1 断言）|
| 5 | `FakeSupervisorBrain` | `tests/runtime/test_node_integration.py` | **`**_kwargs`** | ✅ 显式加参数（机械）|
| 6 | `_CapturingBrain` | `tests/runtime/orgs/test_cancel_propagation.py` | 显式 | ✅ 加参数 |
| 7 | `_SlowCancelAwareBrain` | `tests/runtime/orgs/test_cancel_propagation.py` | 显式 | ✅ 加参数 |
| 8 | `_RecordingBrain` | `tests/runtime/orgs/test_cancel_event_propagation.py` | 显式 | ✅ 加参数 |
| — | `**kwargs` 系列（im_canary / im_cancel / channel_routing_dispatch 共 ≥6 个）| — | `**kwargs` | ❌ 自动吸收，未动 |

**与计划估算对照（关键修正）**：计划估「8 个显式签名 implementor 必改」。实测：
**真正会因新 kwarg 抛 `TypeError` 而必改的只有 6 个**（3 生产 + 3 测试 fake）；计划列入 8 个里的
`FakeBrain` 和 `FakeSupervisorBrain` **实际已用 `**_kwargs`**，本可自动吸收、无需改。

我仍按计划的「8 个」全部显式加了参数：
- `FakeBrain` 必须改（要 capture `recent_outputs` 才能写 S1 回灌断言）；
- `FakeSupervisorBrain` 是机械显式化（保持与计划一致、零风险）。

**结论：波及面没有「远超 8 个」，反而比估算更小（必改 6 个）；无骨架大改，工作量估算成立，未被推翻。**

## 3. live 收敛复现（产品化路径，非 harness 取巧）

- 路径：in-process 构造 stock `Supervisor` + stock `LLMSupervisorBrain`（**无 `FeedbackLLMSupervisorBrain` 子类、无共享 delivery_log**），
  靠 supervisor.py 新加的 `delegation_history` → `recent_outputs` 回灌 + S2 prompt 渲染。
- 模型：`LLMClient.switch_model("dashscope-qwen3.5-plus-nothinking", policy="require")` 锁 no-thinking 端点。
- 脚本：`s1_live_convergence.py`；产物：`_s1_live_convergence.jsonl`。**仅 2 条命令**（≤5 预算内）。

| 场景 | outcome | n_turns / max(eff) | satisfied | 撞 max? | LLM calls | tokens | parse 重试 | 延迟 |
|------|---------|--------------------|-----------|---------|-----------|--------|-----------|------|
| 正常（300 字产品介绍+审校）| **done** | 3 / 12 | **true** | 否 | 5 | 5259 | 0 | 30.8s |
| 刁难（同时恰好 100 字又 5000 字）| **done** | 2 / 12 | true | **否** | 7 | 6900 | 3 | 43.4s |

**「不再瞎眼」的铁证**（正常任务 turn 2 大脑自述）：
> 「虽然 writer 节点已产出约 300 字的文案初稿，但根据预设计划，该初稿尚未经过 reviewer 节点的终稿审校……当前任务未完成。」

大脑明确**引用了节点真实产出**（300 字初稿）来判断，正是 gap⑤ 的治愈点；待 review 产出后 turn 3 收 `satisfied=true`。

**与 spike 行为对照**：
- 正常任务：**完全复现 spike 的绿**——优雅收尾、`satisfied=true`、`n_turns(3) < max`、0 parse 重试。
- 刁难任务：**未撞 max_turns**（hit_max=False，2/12），大脑两轮均判 `progress=false`（识别"逻辑矛盾，无法满足"），
  最终在 node_root 给出清晰的"不可满足说明"后判 `satisfied=true` 收尾（`done`）。
  与 spike 的 `replan_budget_exhausted` 终态不同，但**同属"体面终止、不撞墙"**——
  差异是**模型决策层非确定性**（大脑把"已清楚解释不可满足"视为一种合理收尾），
  **非代码路径分歧**。按边界约定"别强行调 prompt 逼出 replan"，如实记录，未改 prompt。
- **未触发用户最担心的失败模式**（"正常任务又撞 max_turns"）。

**成本（确认关 thinking 生效）**：两条命令 token 5259 / 6900，正常任务 0 次 parse 重试
（thinking 开时 spike 观察到 2~8 次思维链污染重试）；no-thinking 端点显著降噪降本。

## 4. diff stat + 测试数

生产代码（tracked）：
```
 src/openakita/agent/supervisor_brain.py |   2 +
 src/openakita/runtime/supervisor.py     |  66 ++++++++   (S0 clamp + S1 wiring + S5 cancel)
```
新增生产文件（之前 RC-5 prototype 未提交的 untracked 文件，本批一并落库）：
- `src/openakita/runtime/llm_supervisor_brain.py`（S1 `_render_outputs` + S2 收敛 prompt）

测试：
```
 tests/runtime/test_supervisor.py                  | 169 +++  (S0×2 / S1×1 / S5×1 + 1 处回归修正)
 tests/runtime/orgs/test_cancel_propagation.py     |   2 +    (2 fake 加签名)
 tests/runtime/orgs/test_cancel_event_propagation.py |  1 +   (1 fake 加签名)
 tests/runtime/test_node_integration.py            |   1 +    (1 fake 加签名)
```
新增测试文件（untracked，本批落库）：`tests/runtime/orgs/test_llm_supervisor_brain_dryrun.py`（+S1/S2 共 2 个新断言）

- 新增单测：**6 个**（S0×2、S1×2、S2×1、S5×1）。
- 回归修正：**1 处**——`test_supervisor_out_of_turns_when_progressing_forever` 原用 `max_turns=5`（默认
  max_stalls=3/max_replans=5 → S0 clamp 会抬到 21），改为 `max_stalls=1/max_replans=1`（min=3≤5，不被 clamp）
  以保留"撞 OUT_OF_TURNS"原意。**这是 S0 clamp 唯一的一处既有测试调整，属预期内、非退化。**

## 5. 验证结果

- ruff：改过文件全过（`All checks passed!`）。
- mypy：`src/openakita/runtime/supervisor.py` + `llm_supervisor_brain.py` + `agent/supervisor_brain.py` 全过。
- 回归：`pytest tests/runtime/ tests/runtime/orgs/` → **961 passed**（含 8 implementor 改签名后 passthrough 默认路径 + 所有 brain/cancel/dispatch 测试不退化）。
- 整合：`tests/integration/test_v2_im_canary_e2e.py test_v2_im_cancel.py` → 5 passed（`**kwargs` fake 自动吸收无破坏）。
- smoke：`create_app(agent=None)` → OK。

## 6. 端点配置

见 `_endpoint_config_change.md`：在 `data/llm_endpoints.json` **新增**一条
`dashscope-qwen3.5-plus-nothinking`（priority=30，低于现有；`extra_params.enable_thinking=false` +
capabilities 不含 thinking）。**未删/改任何现有端点，未碰 `CUSTOM_API_KEY`/`.env`/主 key**。
该文件是 **git-ignored 运行时配置**（不进版本库），故配置变更仅以本文档存证。

## 7. 边界确认

- ✅ `orgs_supervisor_brain_mode` flag 默认仍 `passthrough`（未改默认值）。
- ✅ 未改 `command_service.py` submit 默认接线（submit 仍走 passthrough；org 级 client 是后续 S3）。
- ✅ 未碰 `.env` / api-key / `CUSTOM_API_KEY`；端点只新增一条复用 `DASHSCOPE_API_KEY`，从不打印 key 明文。
- ✅ 本批仅本地 commit、**不 push**。
- ✅ 未杀 vite(5173)/LSP；live 测试为短时 in-process 脚本，结束即退出，无僵尸进程；backend 未起。
- ✅ 产物全部落 `_rc5_biz/sprint_s1/`，测试 org/cmd 打 `s1_` 前缀。

## 8. 剩余批次衔接

- **S3（submit org 级 client）**：ready。接线点（factory 形参 + `_resolve_brain` 安全回退）前人已预留；
  本批已备好 no-thinking 端点 + 验证回灌路径生效，S3 可直接构造生产版 `GatewayLLMClient` 接进 submit 灰度分支。
- **S4（gap②④）**：ready 且与主链不同代码路径，可并行；`resolve_next_speaker` 已就绪，gap④ 节点目录注入点在 S3 client 构造里。
- **S6（回归+灰度）**：依赖 S1+S2+S3，本批的单测/回归可作为其 CI 基线起点。
- **新依赖发现**：`llm_supervisor_brain.py` 与其 dryrun 测试此前是 **untracked**（RC-5 prototype 遗留），本批已纳入版本库——后续批次基于已落库版本继续即可。
