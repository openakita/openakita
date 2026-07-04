# OpenAkita — Skipped Items Roadmap

> Single source of truth for intentionally-deferred follow-ups uncovered by
> exploratory testing v10/v11 and the deep RCA report.

Tracks intentionally-deferred work items uncovered by exploratory
testing v10 / v11 (`_exploratory_test_report_v10.md`,
`_exploratory_test_report_v11.md`) and the deep RCA report
(`_skip_items_rca_v11.md`).

This document is the single source of truth that every TODO / NOTE /
ROADMAP block in the codebase about skipped items links back to. AI
agents working on plugin manifests, the legacy 308 shim, template
responses, or LLM tool budgets should consult this file FIRST, before
the upstream code or the RCA report.

Baseline commit: `65af00e7` (revamp/v3-orgs) — landed Fix-G1 through
Fix-G6 from RCA v11. Everything below is what was *deliberately not
done* in that wave because the change either needs evidence we don't
have yet (Phase 2 backfill, shim removal) or a deprecation window we
haven't paid for yet (Phase 3 schema escalation).

---

## A.1 Plugin tool_classes Phase 2 — incremental backfill

| Field | Value |
|-------|-------|
| Status | In progress (opportunistic). |
| Tool | `scripts/audit_tool_classes.py` (added with this roadmap). |
| Trigger | Plugin maintenance touches a manifest, OR monthly CI audit. |
| Cadence | 2–3 plugins per month — bundled with the plugin's other PR. |
| Exit criterion | tool_classes coverage ≥ 95 % across `plugins/**/plugin.json` + `plugins-archive/**/plugin.json`. |
| Owner | Plugin maintainers + reviewers. |
| Cross-ref | `_skip_items_rca_v11.md` §2.2, §2.5 (recommended scheme `A + C, B 短期降噪`). |

### Why this matters

`PluginManager.get_tool_class` reads `manifest.tool_classes` first
(`src/openakita/plugins/manager.py:300`) and falls back to the
classifier heuristics in `core/policy_v2/classifier.py` only when no
explicit mapping is found. Without explicit `tool_classes`, the
heuristics keep mis-classifying common patterns such as
`*_settings_get` (mis-classifies as UNKNOWN → quarantined under
safety-by-default) and `*_image_create` (mis-classifies as
MUTATING_SCOPED instead of NETWORK_OUT). RCA v11 §2.3 lists the
known false-positives.

### How to do it

1. Pick the plugin you're touching. Run:

   ```powershell
   .venv\Scripts\python.exe scripts\audit_tool_classes.py --plugin <id> --format table
   ```

2. The script prints a per-tool suggestion plus a confidence column
   (high / medium / low / unknown) plus evidence (name hits + input
   schema hints + description keywords).
3. Copy the **high-confidence** suggestions into the plugin manifest
   under `tool_classes`. Hand-review medium / low / unknown.
4. Optional: `--apply --plugin <id>` writes back only the
   high-confidence suggestions. Medium / low / unknown rows emit a
   review patch but are never written automatically.

### Pitfalls

- The heuristic only sees names + descriptions + (when present) input
  schema. It cannot know that an SDK function actually leaves the
  host. When in doubt, look at the handler implementation.
- `UNKNOWN` is the safety-by-default class — never apply blindly. A
  human must classify these.
- Do not regenerate the entire `tool_classes` block from the audit
  script — preserve manually-curated entries.

### DO NOT do yet

- Do NOT promote `tool_classes` from optional to required in
  `manifest.py` until coverage hits ≥ 95 %. That belongs to §A.2.

---

## A.2 Plugin manifest tool_classes Phase 3 — schema escalation

| Field | Value |
|-------|-------|
| Status | Planned for OpenAkita 2.0 major. |
| Prereq | §A.1 ≥ 95 % coverage + SDK codemod (`scripts/audit_tool_classes.py --apply`) is stable. |
| Migration | 3-release deprecation cycle (see below). |
| Cross-ref | `_skip_items_rca_v11.md` §2.5 Phase 3. |

### Migration path

| Release | Behaviour | `_validate_tool_classes_completeness` mode |
|---------|-----------|-------------------------------------------|
| N (current) | Tool-classes optional; classifier heuristics fill the gap. | `off` (stub already present in `installer.py`). |
| N+1 | WARN at install time when missing. | `warn` |
| N+2 (2.0 major) | ERROR at install time. Opt-out flag: `--allow-missing-classes`. | `error` |
| N+3 | Remove the opt-out flag. | `error` (no opt-out). |

### DO NOT do yet

- Do NOT flip the default mode away from `off` in this branch. Any
  plugin not yet covered by §A.1 will fail to install.
- Do NOT change the manifest schema to mark `tool_classes` `required`
  in `manifest.py` until coverage ≥ 95 % + the codemod is stable.

The stub `_validate_tool_classes_completeness` in
`src/openakita/plugins/installer.py` exists as the future hook
point — wire it from `install_from_path` / `install_from_url` /
`install_from_git` when ready.

---

## A.3 Legacy 308 redirect shim removal

| Field | Value |
|-------|-------|
| Status | Deprecation marker applied (commit `65af00e7`, Fix-G5). |
| Target | OpenAkita 2.1.0 minor. |
| Decision data | `GET /api/diagnostics/legacy-shim-stats` (added with this roadmap). |
| Exit criterion | `hits` for every shim path stays at 0 for ≥ 30 days past the `Sunset: 2026-12-01` header. |
| Cross-ref | `_skip_items_rca_v11.md` §3, `docs/adr/0015-308-shim-retirement-governance.md`. |

### Current state (recap)

The shim at `src/openakita/api/routes/_orgs_v2_legacy_redirects.py`
exposes nine paths under `/api/v2/orgs[/...]`. Eight of them are
already shadowed by `orgs_v2_runtime.router`; only
`POST /api/v2/orgs/templates/{id}/instantiate` is still effective.
Every response carries RFC 8594 `Deprecation: true` + `Sunset:
2026-12-01` headers.

### How the counter works

`_orgs_v2_legacy_redirects.py` keeps a thread-safe `Counter` keyed on
the requested path. Every shim handler calls `_record_shim_hit` before
issuing the 308. The counter survives process lifetime (in-memory),
not restarts — it is intentionally a low-cost observability primitive,
not a persistent metric. Pair with log scraping for long-window
evidence.

### Action steps when ready to remove

1. Poll `GET /api/diagnostics/legacy-shim-stats` daily; record
   `hits` per path.
2. After the 30-day Sunset window with `hits == 0` for every path,
   open a removal PR that:
   - Deletes `src/openakita/api/routes/_orgs_v2_legacy_redirects.py`
   - Deletes the `app.include_router(_orgs_v2_legacy_redirects.router)`
     line in `src/openakita/api/server.py` (see ROADMAP block above
     that line).
   - Drops the `get_shim_hit_stats` import from
     `src/openakita/api/routes/health.py` and the
     `/api/diagnostics/legacy-shim-stats` endpoint, or repurposes it.
3. Update `docs/adr/0015-308-shim-retirement-governance.md` with the
   final removal commit hash.
4. Move this section under `## Completed`.

### DO NOT do yet

- Do NOT delete the shim file or its include_router line in 2.0.x —
  the 30-day evidence window must elapse first.

---

## A.4 spec/runtime template response format unification

| Field | Value |
|-------|-------|
| Status | Design locked, implementation pending P9.7gamma. |
| Direction | Change spec endpoint from `{"templates": [...], "count": N}` envelope to a bare JSON array, matching the runtime endpoint. |
| Cross-ref | `_skip_items_rca_v11.md` §4.3. |

### Affected files

- `src/openakita/api/routes/orgs_v2.py::list_templates` (spec, currently envelope).
- `src/openakita/api/routes/orgs_v2_runtime_orgs.py::list_templates` (runtime, currently bare array — keep).
- `tests/api/contracts/test_orgs_v2_spec.py` (if any envelope assertion).
- `tests/api/contracts/test_orgs_v2.py` (runtime contract).

### Frontend impact

Zero. `apps/setup-center/src/api/orgs.ts` only talks to the runtime
endpoints. See the NOTE block at the top of that file.

### Action steps

1. Land the change in `list_templates` (spec) — return
   `[spec.to_jsonable() for spec in GLOBAL_REGISTRY.list()]`.
2. Update any contract test that pins the envelope shape.
3. Bump the spec response shape in `docs/api/openapi-surface.md`.
4. Drop the ROADMAP comment in `orgs_v2.py::list_templates`.
5. Move this section under `## Completed`.

### DO NOT do yet

- Do NOT change the runtime endpoint. Frontend depends on the bare
  array there.
- Do NOT add a third shape (e.g. `{"items": [...]}`); pick the runtime
  shape and converge.

---

## A.5 Lazy tool loading (deferred epic, NOT for this milestone)

| Field | Value |
|-------|-------|
| Status | Epic, not scheduled. |
| Cross-ref | `_skip_items_rca_v11.md` §1.4 (方案 C). |

### Trigger conditions (at least one must be met)

- Plugin count installed in a typical deployment > 30, OR
- Single main-chat turn cost in tokens > the threshold set by
  ops / billing, OR
- Single main-chat turn wall-clock > 20 s repeatedly attributable to
  schema delivery cost.

### ROI estimate

`-6 K token / turn` on the main chat when the lazy loader plus
`tool_search` round-trip pattern stabilises. Sub-agent benefit is
smaller (sub-agents already use a reduced set today).

### DO NOT do yet

- Only start this epic when at least one trigger above is met.
- The current stable tool set (Fix-G3 + Fix-G4 in `65af00e7`) is the
  agreed baseline — do not refactor `_effective_tools` or
  `_convert_tools_to_llm` without an explicit lazy-loading charter.
- Sub-agent tool isolation is already enforced via
  `_agent_tool_names`; do not regress that for the sake of lazy
  loading.

---

## B.1 RC-5 组织编排 LLM 真编排大脑（gap⑤）

| Field | Value |
|-------|-------|
| Status | **S0-S6 全部落地，灰度默认 passthrough，按 org 可开 llm。** 编排层 + 交付闭环已验证可用。 |
| Completed sprints | S0-S5（第一批，2026-05-29）；S3+S4+S6（第二批，2026-05-29）；交付闭环验证（第三批，2026-05-29）。 |
| Commits (RC-5) | `b72b7477` `ce13d884` `11b04efa` `325de6bf` `b4f95294` `97b8a70c` |
| Graylaunch guide | `_rc5_biz/sprint_s2/_graylaunch_howto.md` |
| Cross-ref | `_rc5_biz/rc5_rca_report.md`、`_rc5_biz/sprint_plan/sprint_implementation_plan.md`、`_rc5_biz/sprint_s1/s1_report.md`、`_rc5_biz/sprint_s2/s2_report.md`、`_rc5_biz/sprint_s3/s3_report.md`、`_rc5_biz/closing/_night_summary.md` |

### 背景（一句话）

RC-5 RCA 发现真编排大脑从未实现——`supervisor_factory.py:270` 固定注入 `PassThroughSupervisorBrain`（"turn 2 必 DONE"），`skipped-items-roadmap.md` 和 `_skip_items_rca_v11.md` 均无 RC-5 条目（遗忘的 follow-up，非刻意冻结）。gap⑤ sprint 补全了这一缺口。

### 已完成（S0-S6 + 交付闭环）

| Stage | 说明 | 关键指标 |
|-------|------|---------|
| S0 参数 clamp | `max_turns` 防呆，保证 replan 预算可达 | 单测绿 |
| S1 delegation_history 回灌 | 节点真实产出喂回 `emit_progress_ledger`，解除"瞎眼" | 961 passed；live 铁证"大脑引用了 300 字初稿" |
| S2 收敛 prompt 固化 | `=== ACTUAL OUTPUTS ===` 区块 + 三条收敛 Decision rules | 正常任务 3 turn satisfied=true |
| S5 cancel 终态 | `Supervisor.run` 接住 `UserCancelledError` | 单测绿 |
| S3 HTTP submit 灰度接线 | `orgs_supervisor_llm_org_allowlist` 白名单 + `GatewaySupervisorLLMClient` | passthrough org 隔离铁证：turn=2/6.1s |
| S4 gap②④ | deliver 层角色名→node_id 解析；节点目录从 OrgV2 store 注入 | 地址解析单测 211 行 |
| S6 收敛+灰度回归 | `test_supervisor_convergence_regression.py`（mock client，CI 安全） | 收敛/灰度/参数边界全绿 |
| 交付闭环（Sprint S3） | 生产工具链本已挂好；补真 Agent 后验证"真产出文件→收敛交付" | product_intro.md(993B)/collab_article.md(716B) done |

### 当前状态（v34 后）

- **灰度默认 passthrough**：`orgs_supervisor_brain_mode` 默认 `passthrough`，`orgs_supervisor_llm_org_allowlist` 默认空。
- **LLM 编排可用**：按 org 白名单开启后，正常产文类任务能多轮收敛优雅 done（live 验证 3/3 正常任务绿）。
- **安全回退**：`_resolve_brain` 保证"flag=llm 但无 client → 回退 passthrough"，接线半成品不崩生产。
- **cancel/stall/replan 全通**：RC-4 桥端到端可断；刁难任务 replan 正常触发；矛盾任务体面不收敛。

### 剩余项（非阻塞，按优先级）

| 项目 | 优先级 | 说明 |
|------|-------|------|
| per-role 便宜模型分层 | P2 | facts/plan/progress_ledger 走不同便宜模型降成本；`GatewaySupervisorLLMClient` 已留 deferred 注释 |
| 节点多轮 ReAct（`MAX_TOOL_ROUNDS > 1`） | P2 | 当前 MAX_TOOL_ROUNDS=1，写文件类够用；"读-改-验证"等复杂工作流需放开 |
| 主端点 403（ctaigw `custom-qwen3.5-plus`）修复 | P1（用户侧） | api-key/配额问题，当前靠 failover 兜住；需用户侧修复 |
| reviewer 抢读时序 | P3 | reviewer 偶发在 write_file 完成前抢读；不影响收敛，可在 prompt 强化串行约束 |
| `delegation_history` 进 checkpoint | P3 | resume 后 history 为空（有意简化）；低频路径，后续按需补 |

### 灰度开启方式

见 `_rc5_biz/sprint_s2/_graylaunch_howto.md`：

```bash
# 方式 A（推荐）：按 org 白名单
ORGS_SUPERVISOR_LLM_ORG_ALLOWLIST=org_abc,org_xyz

# 方式 B（谨慎）：全量
ORGS_SUPERVISOR_BRAIN_MODE=llm
```

### DO NOT do yet

- Do NOT change `orgs_supervisor_brain_mode` default from `passthrough` without graylaunch evidence from production orgs.
- Do NOT remove `PassThroughSupervisorBrain` — it is the permanent safety-net fallback referenced by `_resolve_brain`.
- Do NOT increase `MAX_TOOL_ROUNDS` globally before validating per-org behavior under the node's tool budget (see remaining item above).

---

## B.2 Phase A Graceful Shutdown 治根

| Field | Value |
|-------|-------|
| Status | **≤15s 硬目标达成；≤10s SLO 已达边缘（Sprint 17 smoke p50 ≈ 9.3s）；force-exit watchdog 退役为纯兜底。** |
| Sprints | Sprint 14-17（v31–v34），2026-05-28 – 2026-05-29 |
| Commits (Phase A) | `206c08ab` `07d9757f` `fb1e4e68` `77bb820e` `bbc6c542` `99a9403c` `d74ce574` `a975fad8` |
| Cross-ref | `_rc5_biz/closing/_night_summary.md`（总结） |

### 背景与根因

历史（v23–v30）：每次重启必须等 13–20s 然后人工 kill，os._exit 兜底是日常。

**真凶（v33 诊断）**：`aiosqlite/core.py:90` 每条连接的 `_connection_worker_thread` 不是 daemon 线程；serve-mode 从未调 `pm.unload_plugin()`，所以 plugin `on_unload`（已含 `await self._tm.close()`）从未触发，14 个 aiosqlite worker 线程泄漏，钉住 Python interpreter teardown ~13s。

### 治法（按 Sprint 顺序）

| Sprint | Commit | 变更 | 数据 |
|--------|--------|------|------|
| 14 (v31) | `206c08ab` | IM gateway 并发关闭 + per-adapter wait_for(8s) + os._exit safety net + `openakita stop` CLI | 首次结构改善 |
| 14 (v32-c1) | `07d9757f` | asyncio watchdog → threading.Timer（修复 lifespan teardown 会 cancel asyncio task 的 bug） | watchdog 真正生效 |
| 14 (v32-c2) | `fb1e4e68` | lifespan-to-exit 线程诊断模块 + uvicorn graceful shutdown timeout=3s | 定位 14 个 aiosqlite 线程 |
| 14 (v32-c3) | `77bb820e` | CLI stop 禁用 httpx env-proxy（解决 dev 机代理误拦截） | CLI stop 正确返回 exit 2 |
| 15-16 (v33) | `bbc6c542` | **治真凶**：新增 `PluginManager.unload_all_plugins` + lifespan shutdown handler 驱动 plugin close | 线程存活 17→3 |
| 16 (v33) | `99a9403c` | plugin unload 串行→并行（Semaphore cap=8） | shutdown_to_exit_s **16.69→11.39s**；force_exit_observed True→**False** |
| 17 (v34) | `d74ce574` | IM drain 阶段 gateway+pool 并行 + 各阶段 wait_for | 结构加固 |
| 17 (v34) | `a975fad8` | wework_ws/qqbot WS adapter force-close path（2s 硬截止） | smoke 预期 p50 **~9.3s** |

### 终态（v34 后）

- shutdown_to_exit_s p50 ≈ **9.3s**（v34 smoke 预测；≤10s SLO 边缘）
- non-daemon 线程存活：**3**（Main + 2 asyncio loops；0 aiosqlite workers）
- `force_exit_observed`：**False**（graceful 路径主动退出，watchdog 纯兜底）
- `openakita stop` CLI 正常，代理/死端口两路均处理正确

### 剩余项（非阻塞）

| 项目 | 优先级 | 说明 |
|------|-------|------|
| P1-C：慢 plugin unload 优化 | P2（可选） | 3 个插件（media-strategy/omni-post/manga-studio）on_unload 仍耗 ~3.4s；可优化 poll loop 为 daemon 化或缩短 poll 等待 |
| v34 正式收官复盘 | P2 | Sprint 17 的 v34 PHASEA e2e 因 API 限额（ctaigw 403）未跑成，9.3s 来自 smoke 预测；建议端点修复后补跑确认 |

### DO NOT do yet

- Do NOT remove the `threading.Timer` force-exit watchdog — it is a permanent safety net and costs nothing (idle Thread sleep).
- Do NOT merge plugin unload with Agent.shutdown ordering before auditing the `unload_plugins=False` opt-out callers.

---

## C. Upstream `core/agent.py` merge follow-ups (Batch C)

| Field | Value |
|-------|-------|
| Status | **Done.** Safe subset (#1/#8/#9/#13) ported in the same wave; the higher-risk behaviour ports (C.1/C.2/C.3) are now all ported into `_agent_legacy.py` / `_reasoning_engine_legacy.py` and every previously-skipped test is re-enabled and green. See "Ported in Batch C" below. |
| Trigger merge | `git merge origin/main` (long-overdue upstream sync). Local kept the ADR-0003 split (`core/agent.py` → `openakita.agent` subpackage + `core/_agent_legacy.py`); upstream's fixes that landed on the *monolithic* `agent.py` / `reasoning_engine.py` could not be cherry-picked verbatim. |
| Compat shims added | `core/agent.py`, `core/reasoning_engine.py`, `core/tool_executor.py` (thin re-exports), plus `core/identity.py` now re-exports the hash helpers. These keep `from openakita.core.agent import Agent` (and friends) resolving for legacy code and upstream tests. |
| Owner | Core/agent maintainers. |
| Cross-ref | Audit transcripts (deep analysis 2026-06-26). RCA v11 §4.4 (single-hop delegation), `docs/architecture/conversation_concurrency.md`. |

### Why this matters / how the decision was made

A per-commit audit of the 14 upstream `core/agent.py` fixes found three
classes of outcome:

1. **Already equivalent** locally (most `llm/`, `tool_interrupt_behavior`,
   `agent_state` primitives, `double_texting`, `conversation_lifecycle`,
   `context_stats`, serve-before-agent ordering) — nothing to do.
2. **Safe subset** — self-contained fixes whose capability already existed
   locally and only needed one or two lines of wiring or a defensive
   branch. **Ported now** (see "Ported in this wave" below).
3. **Batch C (this section)** — behaviour that the local refactor relocated
   or never ported; porting touches the hot reasoning loop, the agent
   prompt chain, or needs a new shared module. **Now ported** (see
   "Ported in Batch C" below); all previously-skipped tests are re-enabled
   and green.

### Ported in this wave (safe subset — NOT deferred)

| # | Upstream | What | Local landing |
|---|----------|------|---------------|
| #1 | `86914fc2` | #581 Windows responses-format crashes: soft-hint list branch, plugin-context list injection, `safe_urlparse` migration | `_agent_legacy.py` soft-hint, `_reasoning_engine_legacy.py` plugin inject, `_context_manager_legacy.py` + `agent/skill_manager.py` urlparse |
| #8 | `4dcef3b9` | `Agent.initialize` single-flight lock + `_initialize_unlocked` split | `_agent_legacy.py` `__init__` + `initialize` |
| #9 | `78b5639b` | Skip disabled external skills before SKILL.md parse (preparse allowlist filter) | `_agent_legacy.py::propagate_skill_change`, `agent/skill_manager.py::load_installed_skills` |
| #13 | `09f55110` | Context progress bar: `_extract_usage_summary` via `get_context_snapshot` | `_agent_legacy.py::_extract_usage_summary` |

### Ported in Batch C — analysis + landing

> **Status (this wave): all of C.1 / C.2 / C.3 are ported and green.**
> Deviation from the original recommendation: `_preempt_or_queue_prev_task`
> + `_append_preempt_marker` were ported **directly into `_agent_legacy.py`**
> (not a separate `core/preempt_protocol.py` module). Rationale: it matches
> upstream's structure 1:1, keeps the in-flight/settle primitives (already
> local in `agent_state.py`) as the single source of truth, and satisfies the
> upstream wiring-contract tests without inventing a new seam. The
> HTTP/lifecycle path keeps its own (locally-superior) STEER-default handling.
> Landing summary per item is in each subsection's "Local landing" row.

#### C.1 — Conversation concurrency v1.28 (preempt / interrupt-downgrade / steer done-drain / cancel-resume)

| Dimension | Assessment |
|-----------|------------|
| Upstream commits | `dfa0b2c1` (S1+), `127681c6` (desktop STEER default + QUEUE timeout), `99d20042` (S4 INTERRUPT→QUEUE downgrade), `796c81f0` (S4 MCP-aware + block-tool extension), `9af8257c` (steer done-drain, + deps `4b8f4a2f` / `a8304a0f`), `#608` cancel-resume wiring |
| Type | Single-conversation single-flight; double-texting (REJECT/QUEUE/INTERRUPT/STEER); preempt + settle/abandon; timeout decoupling; tool-interrupt semantics |
| Problem solved | `#572` (`completed → reasoning` crash on double-send), half-written files when block tools are cancelled, 6s QUEUE false-timeouts, steered follow-ups dropped at final-answer, cancelled turn re-doing tool work (`#608`) |
| Merge value | **High** for IM/CLI + INTERRUPT paths and `#572` regression safety |
| Local vs upstream | **Desktop HTTP path (STEER default + long QUEUE timeout) is locally superior / cleaner** — lifted to `conversation_lifecycle.py` + `chat.py` by design (`docs/architecture/conversation_concurrency.md`). Infra is all present (`TaskState.settled/abandoned/in_flight_tools`, `tool_interrupt_behavior.has_any_block_in_flight`, `double_texting.py`, config keys). **Missing**: the agent-layer orchestrator `_preempt_or_queue_prev_task` / `_append_preempt_marker`, reasoning `mark_settled()` + `abandoned` checks, INTERRUPT actually cancelling the old task, QUEUE block-extension, telemetry wiring, `warn_unclassified_tools` startup call |
| Equivalent impl? | Partial (HTTP/lifecycle layer only). No agent-layer equivalent for IM/CLI or INTERRUPT cancel |
| Recommended merge | **Do NOT restore the monolith.** Add a shared `core/preempt_protocol.py` (extract upstream `_preempt_or_queue_prev_task` as pure async logic), call it from both the legacy agent (`chat_with_session*`) and the HTTP INTERRUPT path; add `mark_settled()`/`abandoned` to `_reasoning_engine_legacy`; wire `warn_unclassified_tools` at startup. Priority order: preempt module → reasoning settle/abandon → INTERRUPT downgrade + block extension → metrics |
| **Local landing (done)** | `_agent_legacy.py`: `_preempt_or_queue_prev_task` + `_append_preempt_marker` (INTERRUPT→QUEUE downgrade, MCP-aware block detection, QUEUE block-extension, metrics), wired into both `chat_with_session` + `chat_with_session_stream` (replacing the legacy clear_skip/drain block). `_reasoning_engine_legacy.py`: `_drain_steer_before_finish` (steer done-drain) wired into `reason_stream` + `Agent.execute_task`; cancel-resume `#608` (`_resume_eligible` / `_maybe_persist|load|clear_resume_*`); reason_stream race-guard (`ensure_ready_for_reasoning` + `IllegalReasoningEntry` + `inc_illegal_reasoning_entry` for `reason_stream_iter` / `reason_stream_outer` / `run_impl_*`) + `_handle_llm_error` MODEL_SWITCHING guard. Tool-executor begin/end + in-flight primitives were already local. Tests: `test_conversation_concurrency` / `test_interrupt_downgrade` (110) + `test_steer_done_drain` (19) + `test_cancel_resume_wiring` (23) + `test_reason_stream_state_race` (31) all green |

#### C.2 — Custom-agent identity / first-person voice / profile identity export

| Dimension | Assessment |
|-----------|------------|
| Upstream commits | `150ee738` (SOUL `{{agent_name}}` + builder `agent_voice` + `Agent._resolve_agent_voice`), `664debe3` (profile identity export + `_prepare_prompt_identity_dir` + `identity_dir`), `1a3c3911` (placeholders across all prompt paths + `sync_templates=False` + content-safety voice + bundled fallback) |
| Type | Multi-agent identity isolation + prompt personalization (first-person voice) + cache-key/identity-dir correctness + profile identity-file round-trip |
| Problem solved | After renaming a profile, chat still says "OpenAkita"; custom profile SOUL/AGENT not taking effect (reads global identity); identity files lost on import/export |
| Merge value | **High** for multi-agent / custom-profile UX |
| Local vs upstream | Not superior — this is a bug-fix direction. **Capability layer is already merged**: `prompt/builder.py::_resolve_agent_voice` (module-level), assembler `agent_voice`/`identity_dir` params, `identity_resolver`, SOUL `{{agent_name}}` templates, and `agent/identity.py` already has `_file_hash`/`_load_hashes`/`_save_hashes`/`_HASH_FILE`. **Missing**: Agent never threads `agent_voice`/`identity_dir` into the sync/async prompt build + cache key + fast-reply + `reason_stream`; `agent/identity.py::get_system_prompt` still hardcodes "OpenAkita" (no `agent_voice` param), and lacks `_resolve_bundled_identity_template` + `sync_templates` |
| Equivalent impl? | No end-to-end equivalent. `identity_resolver` passes `sync_templates=False` but `Identity.__init__` has no such param (latent TypeError if that path runs) |
| Recommended merge | Low–medium risk: port `_resolve_agent_voice` + `_prepare_prompt_identity_dir` into `_agent_legacy.py`, wire 4 call sites + extend cache key; port the `agent/identity.py` delta from `1a3c3911` (agent_voice param, `_apply_agent_name_placeholder`, `_resolve_bundled_identity_template`, `sync_templates`); parameterize the reasoning content-safety minimal prompt |
| **Local landing (done)** | `_agent_legacy.py`: `_resolve_agent_voice` + `_prepare_prompt_identity_dir` wired into prompt build + `reason_stream`/`run` (`agent_voice` threaded through). `agent/identity.py`: `agent_voice` param on `get_system_prompt`, `_apply_agent_name_placeholder`, `_resolve_bundled_identity_template`, `sync_templates`, atomic hash IO; `core/identity.py` shim re-exports the placeholder/template helpers. Content-safety minimal prompt parameterized via `_content_safety_identity`. Tests: `test_profile_identity_prompt`, `test_identity`, `test_prompt_compiler::TestAgentResolveVoice` all green |

#### C.3 — Image-turn fast-reply gating + vision-unavailable notice

| Dimension | Assessment |
|-----------|------------|
| Upstream commit | `5e30851f` |
| Type | Bugfix / UX |
| Problem solved | Image/media turns must NOT take the lightweight `think_lightweight` fast path ("chats back without seeing the image"); when no vision endpoint is configured, inject an explicit notice (with filenames/paths) so the model tells the user it cannot read images |
| Merge value | **High** for desktop/IM image flows |
| Local vs upstream | **Mostly missing.** Local `_has_pending_image_attachments` only gates the CHAT-intent downgrade, not fast-reply. No `_format_vision_unavailable_notice` / `_has_pending_media_or_attachments` / `_allows_lightweight_fast_reply` |
| Equivalent impl? | No |
| Recommended merge | Add notice/gating helpers to `runtime/desktop/attachments.py`; set `_current_turn_has_media_attachments` after reading pending attachments; gate the two fast-reply sites; align desktop + IM degraded-image copy. Needs an image regression test → its own PR |
| **Local landing (done)** | `runtime/desktop/attachments.py`: `format_vision_unavailable_notice` / `has_pending_media_or_attachments` / `allows_lightweight_fast_reply` (aliased into `_agent_legacy.py`, re-exported from `core/agent.py` shim). `_agent_legacy.py`: `_current_turn_has_media_attachments` set after reading pending media; both fast-reply sites gated via `_allows_lightweight_fast_reply`; desktop no-vision image branch + IM `pending_images` branch both emit the rich notice. Tests: `test_desktop_attachment_reference` (5) green |

### Previously-skipped tests (Batch C) — now all re-enabled

These were pinned to Batch C items and are now **active and green** after
this wave. Table kept for history; "Blocking item" is the item that shipped.

| Test (file::class / test) | Blocking item | Unlock when |
|---------------------------|---------------|-------------|
| `tests/integration/test_conversation_concurrency.py::TestPreemptOrQueueHelper` / `TestPreemptMarker` / `TestPreemptHelperKeyResolution` / `TestQueueTimeoutCancelsOldTask` / `TestPartialTextOnPreempt` | C.1 | `_preempt_or_queue_prev_task` + `_append_preempt_marker` land |
| `tests/integration/test_interrupt_downgrade.py::TestToolExecutorBeginEndWiring` / `TestPreemptDowngradeWhenBlockToolInFlight` / `TestNoDowngradeWhenAllCancelSafe` / `TestUnknownToolDowngrade` / `TestOtherPoliciesUnaffected` / `TestQueueTimeoutBlockExtension` + `TestMcpSubToolEncoding::test_readonly_mcp_does_not_downgrade_interrupt` | C.1 | preempt orchestration + INTERRUPT downgrade land |
| `tests/integration/test_cancel_resume_wiring.py` (module) | C.1 (`#608`) | `ReasoningEngine._resume_eligible` / `_maybe_*_resume_*` wiring lands |
| `tests/unit/test_steer_done_drain.py::TestDrainSteerBeforeFinishBehaviour` / `TestDrainSteerCeilingTermination` / `TestReasonStreamWiringContract` / `TestExecuteTaskWiringContract` / `TestExecuteTaskDoneDrainEndToEnd` | C.1 (`9af8257c`) | `_drain_steer_before_finish` + wiring land (keeps `TestBuildUserInsertMessage` active) |
| `tests/unit/test_reason_stream_state_race.py::TestReasonStreamRaceGuard` / `TestS5AAuditFixes` / `TestIllegalReasoningEntryAlerts` / `TestAllReasoningTransitionsGuarded` / `TestContentSafetyMinimalPromptIdentity` | C.1 + C.2 | `_reason_stream_impl` race-guard + content-safety `agent_voice` land (keeps `TestTerminalToReasoningContract` active) |
| `tests/unit/test_profile_identity_prompt.py` (module) | C.2 | `Agent._prepare_prompt_identity_dir` + Identity `sync_templates` land |
| `tests/unit/test_identity.py::TestSyncIdentityFileBundledFallback` + `TestIdentityLoading::test_get_system_prompt_does_not_inject_openakita_self_identity` / `test_get_system_prompt_replaces_agent_name_placeholder` | C.2 | `agent/identity.py` `agent_voice` + `_resolve_bundled_identity_template` land |
| `tests/component/test_prompt_compiler.py::TestAgentResolveVoice` | C.2 | `Agent._resolve_agent_voice` lands (builder side already green via `TestBuildSystemPrompt`) |

### DO NOT do yet

- Do **NOT** delete the `core/agent.py` / `core/reasoning_engine.py` /
  `core/tool_executor.py` compat shims — legacy imports and upstream tests
  depend on them until the test suite is migrated to `openakita.agent.*`.
- Batch C is **done** (ported into `_agent_legacy.py` /
  `_reasoning_engine_legacy.py`, see "Ported in Batch C"). A future cleanup
  *may* still extract `_preempt_or_queue_prev_task` into a shared
  `core/preempt_protocol.py` so the HTTP INTERRUPT path can reuse it, but
  that is an optional refactor, not a missing merge.

---

## How AI agents should use this file

When you (Claude / GPT / any other agent or human developer) are about
to modify ANY of the following, read the relevant section here AND the
linked RCA section FIRST:

| You are touching… | Read first |
|-------------------|------------|
| a plugin's `plugin.json` manifest | §A.1, run `scripts/audit_tool_classes.py` |
| `src/openakita/plugins/manifest.py` or `installer.py` | §A.2 |
| `src/openakita/api/routes/_orgs_v2_legacy_redirects.py` or `server.py` include_router for it | §A.3 |
| `src/openakita/api/routes/orgs_v2.py::list_templates` | §A.4 |
| `core/_agent_legacy.py::_effective_tools` or `core/_brain_legacy.py::_convert_tools_to_llm` | §A.5 + `_skip_items_rca_v11.md` §1 |
| `runtime/supervisor_factory.py`, `runtime/llm_supervisor_brain.py`, `orgs/command_service.py` (submit path) | §B.1 RC-5 编排大脑 |
| `api/server.py` (shutdown handlers), `plugins/manager.py` (unload), `channels/gateway.py` (stop) | §B.2 Phase A Shutdown |
| `core/_agent_legacy.py` (chat_with_session / preempt), `core/_reasoning_engine_legacy.py` (reason loop), `core/agent.py`/`reasoning_engine.py`/`tool_executor.py` shims | §C (Batch C — `core/agent.py` merge follow-ups) |
| `agent/identity.py`, `prompt/builder.py` (`agent_voice`), `core/identity.py` shim | §C.2 |
| `runtime/desktop/attachments.py` (vision gating / notice) | §C.3 |

Before acting, read the relevant section here AND the linked RCA
section. Skipped items have explicit "DO NOT do yet" notes when
applicable; honour them unless the user explicitly asks for that
change with awareness of the deferral.

---

## How to check if an item is now ready

Each section above lists exit / trigger criteria. To check readiness:

- §A.1: run
  `python scripts/audit_tool_classes.py --all --format table` and look
  at the per-plugin `missing` column plus the overall coverage line.
- §A.2: same as §A.1; only ready when ≥ 95 % coverage.
- §A.3: hit `GET /api/diagnostics/legacy-shim-stats` and confirm
  `hits` is `{}` for ≥ 30 days past the `Sunset` header.
- §A.4: run the spec/runtime template contract diff (add one to
  `tests/api/contracts/test_orgs_v2_spec.py` if missing).
- §A.5: monitor `data/llm_debug/*.json` token totals; compare against
  trigger thresholds.

---

## Updates to this file

When you complete an item:

1. Move its section to `## Completed` at the bottom.
2. Record the merge date and commit hash.
3. Keep the section text intact (history matters for future audits).
4. Remove the now-obsolete TODO / ROADMAP blocks from the code that
   linked back to this file.

Do not delete history.

---

## Completed

### Batch C — `core/agent.py` merge follow-ups (C.1 / C.2 / C.3)

- **Merged**: 2026-06-26 (post upstream `1.27.25` sync; not yet pushed).
- **What**: full port of the upstream conversation-concurrency v1.28 work
  (preempt/queue + INTERRUPT→QUEUE downgrade + steer done-drain +
  cancel-resume `#608` + reason_stream race-guard), custom-agent
  identity/first-person voice, and image-turn fast-reply gating +
  vision-unavailable notice. See the per-item "Local landing (done)" rows
  in "Ported in Batch C" above.
- **Deviation**: `_preempt_or_queue_prev_task` / `_append_preempt_marker`
  live in `_agent_legacy.py` (matching upstream structure + wiring-contract
  tests) rather than a new `core/preempt_protocol.py`. Optional future
  refactor noted in "DO NOT do yet".
- **Tests re-enabled & green**: `test_conversation_concurrency`,
  `test_interrupt_downgrade`, `test_steer_done_drain`,
  `test_cancel_resume_wiring`, `test_reason_stream_state_race`,
  `test_profile_identity_prompt`, `test_identity`, `test_prompt_compiler`,
  `test_desktop_attachment_reference` (242 total in the Batch C set).
