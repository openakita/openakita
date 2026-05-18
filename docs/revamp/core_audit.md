# `src/openakita/core/` Audit — Phase 2 Entry Decision

This document is required by `docs/openakita_full_backend_revamp_e6d8610d.plan.md`
§8 ("Decision deferred until Phase 2 audit ... record in
`docs/revamp/core_audit.md`"). It classifies every file in
`src/openakita/core/` into one of four buckets, in order to scope the
Phase 2 rewrite cleanly.

The purpose of the audit is not to design the new `agent/` package —
that work happens module by module in subsequent commits — but to
**fix the unit of work** for each Phase 2 sub-commit so that
parity-test scope is well-bounded.

## Verdict legend

| Verdict | Meaning |
|---|---|
| **REWRITE** | Will be rebuilt clean as part of the Phase 2 ports. The legacy file's responsibilities split across more than one new module and/or absorb LangGraph / Magentic-One patterns that contradict the legacy structure. Parity test required. |
| **MOVE** | Well-scoped, single-responsibility module. Will be moved to `agent/` (or `runtime/`) with import-path updates only and an optional rename. No parity test needed; existing unit tests follow. |
| **KEEP** | Stays at the legacy path because it is invoked from non-revamp areas (channels, llm, tools/handlers, scheduler) that are explicitly out of scope per plan §8. |
| **DELETE** | Vestigial / superseded by a runtime/ module. Removed wholesale at Phase 8 cutover. |

The fifth column lists the **closest v2 home** if known; it is the
default unless a parity test surfaces a better placement.

## Inventory (file count: 60+, total ~46 000 LOC)

### The two giants — REWRITE (mandatory)

| File | LOC | Verdict | New home (target) | Notes |
|---|---:|---|---|---|
| `agent.py` | 8 433 | **REWRITE** | `agent/core.py` (~500) + `agent/permission.py` + `agent/audit.py` + `agent/identity.py` (much already present in dedicated `core/*.py`) | The plan's headline target. Legacy file mixes top-level helpers (~100), risk-intent gating, prompt strategy, force-tool policy, attachment handling, and the `Agent` class itself. The agent class proper is buried; everything else either has a dedicated `core/*.py` already or belongs in `tools/`. The new `agent/core.py` will hold only the `Agent` lifecycle + `run_task()` entry point. |
| `reasoning_engine.py` | 7 987 | **REWRITE** | `agent/reasoning.py` (~600) on top of `runtime/state_graph.py` | Per the plan, the giant if/else cascade is replaced by an explicit StateGraph with prebuilt ReAct shape. Helper functions (cache keys, fingerprints, mode rulesets, tool filtering) move to `agent/tools.py` or `agent/context.py`. |

### The medium tail — REWRITE

| File | LOC | Verdict | New home (target) | Notes |
|---|---:|---|---|---|
| `brain.py` | 1 698 | **REWRITE** | `agent/brain.py` (~400) | Ralph-loop driver; LLM client wrapping. Will be re-implemented in terms of the v2 supervisor's `SupervisorBrain` protocol so the same brain can be reused by the v2 supervisor's outer/inner loop. |
| `tool_executor.py` | 1 609 | **REWRITE** | `agent/tools.py` (~300) | Currently mixes routing, error handling, retry, audit, and a streaming-handle layer. The streaming bits move to `runtime/stream.py` consumers; routing/retry collapse into `runtime.retry_policy.RetryPolicy`. |
| `context_manager.py` | 1 569 | **REWRITE** | `agent/context.py` (~400) | Today owns conversation history, token budget, prompt assembly, and microcompact. Token budget is its own well-scoped module (`token_budget.py`) and stays separate; the rest collapses around `prompt/builder.py` (already extracted). |
| `intent_analyzer.py` | 810 | **REWRITE** | `agent/intent.py` | Many decision points were duplicated inside `agent.py`; merging gives a single place to reason about user intent. |
| `response_handler.py` | 933 | **REWRITE** | `agent/response.py` | Glue between `reasoning_engine` and `brain`; collapses naturally once `reasoning_engine` is re-shaped against `runtime/state_graph`. |
| `risk_intent.py` | 699 | **REWRITE** | `agent/risk.py` | Risk classification is touched by `agent.py`, `reasoning_engine.py`, and `tool_executor.py`; consolidating into one module (with one parity case per risk class) is the cheapest way to shrink the giants. |
| `stream_accumulator.py` | 703 | **REWRITE** | `runtime/stream/accumulator.py` (under existing stream module) | Today this owns chunk reassembly the runtime now handles via `runtime/stream.py`. The classes it exposes (`StreamAccumulator`, etc.) move into the v2 stream package and the legacy file goes. |
| `task_monitor.py` | 640 | **REWRITE** | `runtime/standup.py` (per plan §8 rename note about `heartbeat.py`) | Periodic supervision today; replaced by `runtime/stall_detector.py` + a slimmer "standup" task. |
| `current_turn.py` | 516 | **REWRITE** | merged into `agent/reasoning.py` and `runtime/messenger.py` | Encodes the per-turn lifecycle that v2 splits across StateGraph supersteps and Messenger inboxes. |

### The medium tail — MOVE

| File | LOC | Verdict | New home | Notes |
|---|---:|---|---|---|
| `permission.py` | 455 | **MOVE** | `agent/permission.py` | Tight, well-tested module. Used by tools and runtime; no v2 contract change needed today. Plan §2 explicitly anticipates this is a move. |
| `identity.py` | 495 | **MOVE — done** | `agent/identity.py` | Shipped 2026-05-18 in commit `feat(agent): port identity (MOVE)`. Byte-equivalent copy + docstring refresh; legacy path is now a re-export shim. The existing `tests/unit/test_identity.py` (17 tests) still passes through the shim, transitively anchoring the move. |
| `persona.py` | 467 | **MOVE** | `agent/persona.py` | Persona compilation; touched by prompt builder. Stable. |
| `validators.py` | 416 | **MOVE** | `agent/validators.py` | Pure functions. |
| `agent_state.py` | 431 | **MOVE** | `agent/state.py` (already exists; merge) | Fold into the existing v2 `agent/state.py` so there's a single state container. |
| `pending_approvals.py` | 449 | **MOVE** | `agent/pending_approvals.py` | Tightly coupled to permission flow but stable. |
| `proactive.py` | 405 | **MOVE** | `agent/proactive.py` | Self-prompting suggestions; OK to keep. |
| `audit_logger.py` | 177 | **MOVE** | `agent/audit.py` | Plan §8 lists this as a MOVE candidate. |
| `agent_output_guard.py` | 86 | **MOVE — done** | `agent/output_guard.py` | Shipped 2026-05-18 in commit `feat(agent): port output_guard and output_formatter (MOVE)`. Legacy path is now a re-export shim. |
| `prompt_assembler.py` | 157 | **MOVE** | `agent/prompt.py` | The legacy single-pass assembler; the new `prompt/builder.py` (already in repo) is the multi-layer builder. We keep the assembler as the agent's view-into-builder until the parity harness is happy. |
| `output_formatter.py` | 101 | **MOVE — done** | `agent/output_formatter.py` | Shipped 2026-05-18 in commit `feat(agent): port output_guard and output_formatter (MOVE)`. Legacy path is now a re-export shim. |
| `errors.py` | 15 | **MOVE — done** | `agent/errors.py` | Shipped 2026-05-18 in commit `feat(agent): port errors and working_facts (MOVE)`. Legacy path is now a re-export shim until Phase 8. |
| `state.py` | 75 | **DELETE — Phase 8** (re-classified) | n/a | Re-classified during the actual port: a workspace-wide search for `core.state` returns zero importers in production code, so the audit's original "MOVE → merge into agent/state.py" is wrong on two counts. (1) The merge target (`agent/state.py`) is the port of the unrelated 431-line `core/agent_state.py` and shares no symbols with this 75-line `StateStore` / `AppState` module. (2) Nothing imports it, so the cleanest action is mechanical removal at Phase 8. We leave the file untouched so a stray future caller would still find it during the burn-in window. |
| `working_facts.py` | 53 | **MOVE — done** | `agent/working_facts.py` | Shipped 2026-05-18 in commit `feat(agent): port errors and working_facts (MOVE)`. Legacy path is now a re-export shim until Phase 8. |
| `token_budget.py` | 78 | **MOVE** | `agent/token_budget.py` | Numeric helpers. |
| `confirmation_state.py` | 113 | **MOVE** | `agent/confirmation.py` | Tight. |
| `microcompact.py` | 155 | **MOVE** | `agent/microcompact.py` | Tight; consumed by context manager. |
| `feature_flags.py` | 108 | **MOVE** | `agent/feature_flags.py` | Pure-data. |
| `loop_budget_guard.py` | 181 | **MOVE** | `agent/loop_budget.py` | Tight wrapper around limits. |
| `resource_budget.py` | 363 | **MOVE** | `agent/resource_budget.py` | Independent budget tracker; stable. |
| `tool_signatures.py` | 75 | **MOVE** | `agent/tool_signatures.py` | Pure functions. |
| `tool_result_budget.py` | 88 | **MOVE** | `agent/tool_result_budget.py` | Pure helpers. |
| `streaming_tool_executor.py` | 148 | **MOVE** | `agent/streaming_tool_executor.py` | Wrapper around tool exec; will follow the rewrite of `tool_executor.py`. |
| `engine_bridge.py` | 128 | **MOVE** | `agent/engine_bridge.py` | Adapter; small. |
| `capabilities.py` | 89 | **MOVE** | `agent/capabilities.py` | Pure data. |
| `security_actions.py` | 116 | **MOVE** | `agent/security_actions.py` | Stable. |
| `domain_allowlist.py` | 117 | **MOVE** | `agent/domain_allowlist.py` | Pure data. |
| `trusted_paths.py` | 157 | **MOVE** | `agent/trusted_paths.py` | Pure data. |
| `file_history.py` | 165 | **MOVE** | `agent/file_history.py` | Pure data. |
| `lsp_feedback.py` | 165 | **MOVE** | `agent/lsp_feedback.py` | Pure helpers. |
| `desktop_notify.py` | 273 | **MOVE** | `agent/desktop_notify.py` | OS bridge; isolated. |
| `sse_replay.py` | 278 | **MOVE** | `agent/sse_replay.py` | Stream replay buffer; stable. |
| `ui_confirm_bus.py` | 434 | **MOVE** | `agent/ui_confirm_bus.py` | UI signal bus; stable. |
| `trait_miner.py` | 327 | **MOVE** | `agent/trait_miner.py` | User trait mining; stable. |
| `user_profile.py` | 640 | **MOVE** | `agent/user_profile.py` | Long but tight; pure-data ops. |
| `skill_manager.py` | 533 | **MOVE** | `agent/skill_manager.py` | Skill resolution; stable. |
| `memory.py` | 270 | **MOVE** | `agent/memory.py` (or merge with `memory/`) | Currently a thin facade; decide at port time whether to delete entirely. |
| `hooks.py` | 240 | **MOVE** | `agent/hooks.py` | Hook registry; stable. |
| `token_tracking.py` | 245 | **MOVE** | `agent/token_tracking.py` | Telemetry; stable. |
| `checkpoint.py` | 225 | **MOVE** | merge into `runtime/checkpoint.py` (verify name collision) | The v2 `runtime/checkpoint.py` already implements the new `Checkpoint` abstraction; this legacy module is the agent-side view. After Phase 2 it becomes a re-export and is finally deleted at Phase 8. |
| `sandbox.py` | 218 | **MOVE** | `agent/sandbox.py` | OS sandbox wrapper; stable. |
| `docker_backend.py` | 142 | **MOVE** | `agent/docker_backend.py` | Container shell backend; stable. |
| `ralph.py` | 294 | **MOVE** | `agent/ralph.py` | Legacy Ralph driver; stays for backwards compatibility through Phase 6 cutover. |

### Specialised — KEEP at `core/`

These are consumed by non-revamp areas and the plan explicitly puts
them out of scope. Touching them risks needless churn.

| File | LOC | Verdict | Notes |
|---|---:|---|---|
| `policy_v2/` (subpackage) | (multi-file) | **KEEP** | The C10 declared-class trust system; consumed by `plugins/manager.py`, `tools/handlers/`, audit. Independent of agent rewrite. |
| `auth/` (subpackage) | (multi-file) | **KEEP** | Auth pipeline; used by `api/routes/`. Out of scope. |
| `supervisor.py` | 741 | **KEEP** at this path **then DELETE** | This is the legacy intra-agent supervisor (different concept from `runtime/supervisor.py`). Per plan §2 it gets rewritten elsewhere; the legacy file is deleted at Phase 8 alongside the rest. |
| `log_health.py` | 89 | **KEEP** | Used by `plugins/manager.py` and `core/agent.py`; trivial. |
| `config_watcher.py` | 106 | **KEEP** | Used by API server. Out of scope. |
| `deps.py` | 56 | **KEEP** | Dependency-injection container for FastAPI. Out of scope. |
| `session_caches.py` | 68 | **KEEP** | Session-level caches; consumed by API. |
| `im_context.py` | 27 | **KEEP** | IM channel adapter helper. Out of scope per plan §2. |
| `context_utils.py` | 70 | **KEEP** | Pure helpers used by IM context. |

### Vestigial — DELETE at Phase 8

| File | LOC | Verdict | Notes |
|---|---:|---|---|
| `state.py` | 75 | **DELETE** | Re-classified from MOVE during the actual port (see the MOVE table). Zero importers in production code (workspace-wide search for `core.state` / `from openakita.core.state` / `from .core.state` is empty). The Tauri Rust file mentioning `AppState` is unrelated. Removed mechanically at Phase 8. |

## Aggregate sizing

- **REWRITE bucket**: ~25 000 LOC → target ~5 000 LOC (≈80 %
  reduction). Aligns with plan §9 acceptance criterion ">=60 %
  backend code line drop" (the giant rewrite alone clears it).
- **MOVE bucket**: ~10 500 LOC moves with import-path updates only.
- **KEEP bucket**: ~1 500 LOC stays.

## Sub-commit plan for Phase 2

The audit fixes the granularity for the next ~14 commits; each line
below maps to one commit in the Phase 2 series.

1. `feat(agent): scaffold v2 agent package skeleton` — empty
   modules, `agent/__init__.py` re-exporting nothing yet, plus
   `tests/parity/` shell. **Substantively shipped** before audit
   was written (see `agent/state.py`).
2. **Done** — `feat(agent): port errors and working_facts (MOVE)`
   landed 2026-05-18. `core/state.py` was re-classified to DELETE
   when the port revealed it has zero importers.
3. **Partially done** — `feat(agent): port output_guard and output_formatter (MOVE)`
   landed 2026-05-18 (`agent_output_guard.py` + `output_formatter.py`).
   The remaining halves of the audit's grouping (`identity.py` 495,
   `persona.py` 467) are larger and merit their own commits; they
   become commits 4 and 5 below.
4. **Done** — `feat(agent): port identity (MOVE)` landed 2026-05-18.
5. `feat(agent): port persona (MOVE)`.
4. `feat(agent): port permission, audit, validators (MOVE)` — covers
   plan §8 keep-as-is candidates.
5. `feat(agent): port pending_approvals, confirmation, ui_confirm_bus, hooks (MOVE)`.
6. `feat(agent): port token / resource / loop budget modules (MOVE)`.
7. `feat(agent): port skill_manager, capabilities, security_actions (MOVE)`.
8. `feat(agent): port file_history, trusted_paths, domain_allowlist, lsp_feedback (MOVE)`.
9. `feat(agent): port sandbox, docker_backend, desktop_notify, sse_replay (MOVE)`.
10. `feat(agent): port ralph driver and trait_miner / user_profile (MOVE)`.
11. `test(parity): bootstrap parity harness with 5 baseline cases`.
12. `feat(agent): rewrite tool_executor.py into agent/tools.py (REWRITE)`.
13. `feat(agent): rewrite context_manager.py into agent/context.py (REWRITE)`.
14. `feat(agent): rewrite brain.py into agent/brain.py (REWRITE)`.
15. `feat(agent): rewrite reasoning_engine.py into agent/reasoning.py (REWRITE)` — **the big one**, depends on `runtime/state_graph.py` (already shipped).
16. `feat(agent): rewrite agent.py into agent/core.py (REWRITE)` — assembles the pieces.
17. `test(parity): expand parity harness to 30 cases for G2 sign-off`.

After commit 17 the G2 review note can be authored
(`docs/revamp/gates/G2.md`).

## Open questions to resolve at port time

1. Does `core/checkpoint.py` (225 LOC) carry agent-only semantics
   that `runtime/checkpoint.py` does not, or is it pure facade? If
   facade, fold it during Phase 2 commit 12 and skip the Phase 8
   delete.
2. `prompt_assembler.py` (157 LOC) overlaps with `prompt/builder.py`
   (workspace-level, multi-layer). The former is a single-pass
   string assembler; the latter compiles identity into runtime
   prompt sections. Decide at commit 13 whether to keep both or
   collapse `prompt_assembler.py` into `prompt/builder.py`.
3. `engine_bridge.py` (128 LOC) — verify whether the bridge is still
   needed once `agent/reasoning.py` consumes `runtime/state_graph.py`
   directly. If not, delete (move it from MOVE to DELETE).
