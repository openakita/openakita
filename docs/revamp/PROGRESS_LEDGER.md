# Revamp Progress Ledger

<!-- machine-readable phase marker; do NOT remove.
     Parsed by tests/revamp/_ledger.py + tests/parity/test_no_facade.py. -->
current_phase: P-RC-6

> Source of truth for every commit landed on `revamp/v2` during
> the post-RC continuation phases (P-RC-0 → P-RC-8). One row per
> commit, in commit order. Each row is appended *in the same
> commit that produced it* (or the next commit, when the producing
> commit is the row source itself, i.e. commit 4 of P-RC-0 which
> bootstraps this file).
>
> Rules of the ledger (per continuation plan §0.3):
> * append-only — once a row lands on `revamp/v2` it must not
>   be silently rewritten;
> * `LOC delta` and `tests delta` are signed integers,
>   positive = grew, negative = shrank, `0` = unchanged;
> * `ADR refs` lists the ADRs whose §s the commit implements.
>
> Pause points: every 5 commits, re-read `docs/revamp/STATUS.md`
> + this ledger + the relevant section of the original plan
> (`openakita_full_backend_revamp_e6d8610d.plan.md`) and the
> continuation plan
> (`openakita_revamp_continuation_plan_d6192647.plan.md`) before
> opening the next commit.

## P-RC-0 — Truth alignment & drift guardrails

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `b2abc4db` | P-RC-0 #1 | chore(config): align runtime_v2_enabled doc with its default=True | +22 | 0 | ADR-0010 |
| `15da567e` | P-RC-0 #2 | docs(revamp): write rollback.md (plan §7 mitigation, was missing) | +141 | 0 | ADR-0010 |
| `5f5e269e` | P-RC-0 #3 | chore(repo): gitignore local smoke-e2e artifacts; relocate to tests/artifacts/ | +9 | 0 | — |
| `30d5a287` | P-RC-0 #4 | tooling(revamp): add LOC invariant audit + PROGRESS_LEDGER scaffolding | +380 | +2 | — |
| `528db8d1` | P-RC-0 #5 | test(parity): kill facade-self-equivalence false positives | +278 / -18 | +6 | — |
| _this commit_ | P-RC-0 G | docs(revamp): G-RC-0 gate review (post-RC continuation phase 0 done) | +174 (gate note) + STATUS row + ledger fill-in | 0 | — |

## P-RC-1 — IM truly lands on the v2 Supervisor

G-RC-0 was signed; this phase wires canary IM traffic to the v2
supervisor stack. The first two rows (`P1.0a`/`P1.0b`) close the
two nits the P-RC-0 audit raised before the main work begins.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| 80d23766 | P-RC-1 P1.0a | chore(revamp): standardise progress ledger to advertise current_phase | +60 | 0 | — |
| 9b5671ca | P-RC-1 P1.0b | test(revamp): enforce sentinel expiry against current_phase | +85 | +7 | — |
| 89902514 | P-RC-1 #1 | feat(runtime): add session_bridge for session<->org id lookup | +231 | +8 | ADR-0002 |
| b0653a59 | P-RC-1 #2 | feat(runtime): promote channel_routing helper to async dispatch_inbound_message_to_v2 | +398 / -5 | +6 | ADR-0002, ADR-0004 |
| 1c0a69a3 | P-RC-1 #3 | feat(runtime): add im_stream_bridge to relay StreamBus progress to IM channels | +292 | +7 | ADR-0006 |
| fc2558dd | P-RC-1 #4 | feat(channels): replace canary log hook with real v2 dispatch (canary-org gated) | +125 / -30 | 0 | ADR-0002, ADR-0004 |
| a97fa73b | P-RC-1 #5 | feat(channels): plumb IM cancel verb to runtime CancellationToken (per org) | +47 (gw) +147 (test) | +4 | ADR-0002, ADR-0004 |
| fc701385 | P-RC-1 #6 | feat(config): add runtime_v2_canary_orgs allow-list (default empty) | +37 / -2 (cfg) +45 (test) | +5 | ADR-0002 |
| 4d396303 | P-RC-1 #7 | test(integration): e2e canary org runs via Supervisor + cancel + resume | +273 (test) +11 (gw drain fix) | +1 | ADR-0002, ADR-0004, ADR-0006 |
| _this commit_ | P-RC-1 G | docs(revamp): G-RC-1 gate review + STATUS scoreboard update | +224 (gate) + STATUS row + ledger fill-in | 0 | — |

## P-RC-2 — Frontend v2 wiring

G-RC-1 was signed; this phase wires the v2 supervisor stack into
the setup-center frontend (SSE backend, EventSource client,
ProgressLedgerTimeline, OrgChatPanel switch, TemplatePickerDrawer
mount, stale-bundle banner) and closes the two residual risks the
P-RC-1 gate review flagged: drain-on-close in `StreamBus` (#1) and
cold-session `org_id` rehydration in `MessageGateway` (#3).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `b6a77a94` | P-RC-2 P2.0 | chore(revamp): bump ledger current_phase to P-RC-2 + apply N3/N4/N5 discipline doc | +33 / -2 | 0 | — |
| `112534d5` | P-RC-2 P2.1 | feat(runtime): add drain-on-close semantics to StreamBus | +248 / -16 | +4 | ADR-0006 |
| `2d35c0f9` | P-RC-2 P2.2 | feat(channels): rehydrate cold-session org_id from disk in lookup | +153 / -13 | +6 | ADR-0002 |
| `00c783f3` | P-RC-2 P2.3 | feat(api): add GET /api/v2/orgs/{id}/stream (SSE) backed by StreamBus | +400 / -1 | +5 | ADR-0006 |
| `74d565b7` | P-RC-2 P2.4 | feat(setup-center): add v2 stream client (EventSource wrapper) + vitest infra | +316 (src) +1119 (lock, generated) | +5 (vitest) | ADR-0006 |
| `415226a7` | P-RC-2 P2.5 | feat(setup-center): render ProgressLedgerTimeline component | +219 / -2 | +4 (vitest) | ADR-0006 |
| `a9cd8f82` | P-RC-2 P2.6 | feat(setup-center): OrgChatPanel switches to v2 stream when org is v2-bound | +153 / -2 | +2 (vitest) | ADR-0006 |
| `7bd3c29b` | P-RC-2 P2.7 | feat(setup-center): mount TemplatePickerDrawer in OrgEditorView | +169 / -1 | +1 (vitest) | ADR-0006 |
| `0bfad7de` | P-RC-2 P2.8 | feat(setup-center): bump asset version + stale bundle banner | +339 / -1 | +2 (vitest) +3 (pytest) | ADR-0007 |
| _this commit_ | P-RC-2 P2.9 | docs(revamp): G-RC-2 gate review + STATUS scoreboard update | +220 / 0 | 0 | — |

## P-RC-3 — Multi-process-safe v2 persistence + nit cleanup

G-RC-2 was signed; this phase folds in five small tail items the
P-RC-2 audit identified (T1–T5), then adds a SQLite-backed
`SqliteOrgStore` so multi-process v2 deployments can share a
single org catalogue without JSON-write contention. The phase also
ships a JSON->SQLite migration helper, a pluggable backend factory
gated by `settings.orgs_v2_backend`, and a contract suite that
runs identical cases against both backends.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `f26863c5` | P-RC-3 P3.0 | chore(revamp): bump ledger to P-RC-3 + add commit_guard script (T1) | +332 (script+tests+ledger+ruff) | +11 (commit_guard) | — |
| `20021b71` | P-RC-3 P3.1 | docs(revamp): correct G-RC-2 5ms polling wording (T2) | +4 / -2 (gate doc) | 0 | — |
| `69225c0f` | P-RC-3 P3.2 | feat(runtime): add closed-gate to StreamBus + public subscription API (T3+T5) | +156 / -14 | +5 (closed-gate + public-api) | ADR-0006 |
| `7aabdce3` | P-RC-3 P3.3 | feat(runtime): add idle-bus cleanup to StreamRegistry (T4) | +306 / -6 | +6 (cleanup_idle + periodic + reattach) | ADR-0006 |
| `723fd1d5` | P-RC-3 P3.4 | feat(runtime/orgs): add SqliteOrgStore mirroring JsonOrgStore contract | +371 (sqlite_store + tests) | +9 (CRUD + concurrent + reopen + corrupted) | ADR-0010 |
| `fea6a347` | P-RC-3 P3.5 | feat(runtime/orgs): contract suite shared across Json + Sqlite stores | +205 / -1 (contract test + sqlite trailing nl) | +18 (9 cases x 2 backends) | ADR-0010 |
| `a0339d12` | P-RC-3 P3.6 | feat(runtime/orgs): pluggable backend via settings.orgs_v2_backend (json|sqlite) | +138 / -17 (config + factory + tests) | +6 (config + factory dispatch) | ADR-0010 |
| `4e6d665c` | P-RC-3 P3.7 | feat(scripts): migrate_orgs_v2_json_to_sqlite (idempotent) | +340 / -1 (script + tests + rollback) | +5 (migrate fresh/idempotent/dry-run/malformed/empty) | ADR-0010 |
| _this commit_ | P-RC-3 P3.8 | docs(revamp): G-RC-3 gate review + STATUS scoreboard update | +246 (G-RC-3) + 1 row (STATUS) + sqlite warm-up test harden | 0 | — |

## P-RC-4 — Phase 2 real slim-down: brain / tools / context

G-RC-3 was signed; this phase opens the first real Phase-2 rewrite:
the three medium giants (`core/brain.py` 2015, `core/tool_executor.py`
1818, `core/context_manager.py` 1799) are split into focused
`runtime/llm/*`, `runtime/stream/llm.py`, `runtime/io/*`,
`runtime/context/*` submodules; `agent/{brain,tools,context}.py`
are rewritten on top of those submodules; the three `core/*.py`
giants collapse into `≤30` LOC re-export shims; `LOC_BASELINE.json`
shrinks the giants and bumps the agent files in the same commit so
`scripts/revamp_loc_audit.py` stays exit 0. Real-parity suites land
5 cases per giant (15 total) against recorded LLM fixtures and assert
v1/v2 `__file__` differ -- the facade-detector `test_no_facade.py`
loses its sentinel allowance for these three files.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `4817bf1b` | P-RC-4 P4.0 | chore(revamp): bump ledger to P-RC-4 + raise commit-guard baseline | +1 line (header bump) + this block | 0 | — |
| `f1d947dc` | P-RC-4 P4.1 | refactor(runtime/llm): scaffold EndpointFailoverView extracted from core.brain | +145 (failover.py + __init__.py + ledger row) | +7 (failover view cases incl. async health-check) | ADR-0001, ADR-0002 |
| `210eb39f` | P-RC-4 P4.1b | refactor(core/brain): delegate nine endpoint wrappers to EndpointFailoverView | +35 / -95 (core.brain delegations) | 0 | ADR-0001, ADR-0002 |
| `5e7e0e79` | P-RC-4 P4.2 | refactor(runtime/llm): extract compiler-LLM circuit breaker from core.brain | +280 (circuit_breaker.py + tests + delegation) / -70 (state fields + 3 method bodies + reset block) | +11 (5 transition + 6 auth-keyword param) | ADR-0001, ADR-0002 |
| `2d39b49c` | P-RC-4 P4.3 | refactor(runtime/llm): scaffold multimodal block conversion module | +328 (multimodal.py + tests + __init__ exports + ledger row) | +10 (multimodal conversion cases) | ADR-0001, ADR-0002 |
| `1b77a4ed` | P-RC-4 P4.3b | refactor(core/brain): delegate _convert_response_to_anthropic to multimodal module | +6 / -82 (legacy method body) | 0 | ADR-0001, ADR-0002 |
| `0746709f` | P-RC-4 P4.4 | refactor(runtime/llm): extract LLM streaming primitive from core.brain | +281 (stream.py + tests + __init__ exports) | +5 (stream + tracking cases) | ADR-0001, ADR-0002, ADR-0006 |
| `cdc26689` | P-RC-4 P4.5 | feat(agent): implement real agent/brain.py on extracted helpers (~370 LOC) | +290 (real Brain class + SupervisorBrain protocol + helper accessors + 16 v2 surface methods) / -88 (facade) | 0 (existing tests cover) | ADR-0001, ADR-0003 |
| `7264dcc8` | P-RC-4 P4.6a | refactor(core): rename core/brain.py to core/_brain_legacy.py (pre-shim move) | 0 (pure rename) | 0 | ADR-0001 |
| `dfa462df` | P-RC-4 P4.6b | refactor(core): replace core/brain.py body with 26-LOC lazy-import shim | core/brain 2015 -> 26 (shim, lazy __getattr__) | 0 | ADR-0001, ADR-0003 |
| `4b5d385c` | P-RC-4 P4.7 | test(parity): real parity for Brain (5 fixtures + __file__ divergence) | +176 (test_brain_parity.py) + 5 JSON fixtures | +7 (5 fixtures + __file__ + class-identity) | ADR-0001, ADR-0003 |
| `bf5559e2` | P-RC-4 P4.8 | refactor(runtime/io): extract truncate + overflow from core.tool_executor | +307 (truncate.py + overflow.py + __init__ + tests) | +8 (truncate / overflow / cleanup / constant) | ADR-0001 |
| `0fc70c82` | P-RC-4 P4.9 | refactor(runtime/llm): collapse tool_executor routing/retry into RetryPolicy | +108 (retry_policy.py +50 / test_retry_policy_tool.py +85) | +10 (tool retry predicate + default policy) | ADR-0001, ADR-0004 |
| `b010bdae` | P-RC-4 P4.10pre | refactor(runtime/io): re-anchor v2 smart_truncate marker to legacy Chinese text for byte-faithful parity | +/-3 (truncate.py marker text) | 0 (test updated, no count delta) | ADR-0001 |
| `b57a2ed6` | P-RC-4 P4.10 | feat(agent): implement real agent/tools.py on extracted helpers (~280 LOC) | +346 (agent/tools.py 56->347; +221 real SLOC) | 0 (parity tests already cover behaviour; new methods covered indirectly) | ADR-0001 |
| `cd69cd60` | P-RC-4 P4.11a | refactor(core): rename core/tool_executor.py to _tool_executor_legacy.py (pure git mv) | rename only | 0 | ADR-0001 |
| `8e8e7da7` | P-RC-4 P4.11b | refactor(core): replace core/tool_executor.py body with thin import shim | core/tool_executor.py 1818->41 (lazy __getattr__ re-export); agent/tools.py imports updated to use _tool_executor_legacy | 0 | ADR-0001 |
| `41ca7a94` | P-RC-4 P4.12 | test(parity): real parity for ToolExecutor (5 fixtures + __file__ divergence) | +124 (test_tools_parity.py) + 5 JSON fixtures | +7 (5 fixtures + __file__ + class-identity) | ADR-0001 |
| `9d31e975` | P-RC-4 P4.13a | refactor(runtime/context): extract group_messages + budget_trace from core.context_manager | +279 (grouping.py + budget_trace.py + __init__ + tests) | +11 (group_messages / calc_context_budget / estimate_tokens / payload_size_bytes) | ADR-0001 |
| `3779575b` | P-RC-4 P4.13b | refactor(runtime/context): extract compress (pre_request_cleanup + sanitize_tool_pairs) from core.context_manager | +180 (compress.py + 5 new tests + __init__ extension) | +5 (sanitize / cleanup) | ADR-0001 |
| `5ba4711b` | P-RC-4 P4.14 | feat(agent): implement real agent/context.py on extracted helpers (~340 LOC) | +279 (agent/context.py 57->336; +208 real SLOC) | 0 (parity tests cover behaviour) | ADR-0001 |
| `11eaec49` | P-RC-4 P4.15a | refactor(core): rename core/context_manager.py to _context_manager_legacy.py (pure git mv) | rename only | 0 | ADR-0001 |
| `0af43180` | P-RC-4 P4.15b | refactor(core): replace core/context_manager.py body with thin import shim | core/context_manager.py 1799->36 (lazy __getattr__ re-export); agent/context.py imports updated to use _context_manager_legacy | 0 | ADR-0001 |
| `7b46216e` | P-RC-4 P4.16 | test(parity): real parity for ContextManager (5 fixtures + __file__ divergence) | +112 (test_context_parity.py) + 5 JSON fixtures | +7 (5 fixtures + __file__ + class-identity) | ADR-0001 |
| `456bcb45` | P-RC-4 P4.17 | docs(revamp): G-RC-4 gate review + STATUS scoreboard update | +174 (gates/G-RC-4.md) + STATUS scoreboard row | 0 | ADR-0001 |

## P-RC-5 — Phase 2 real slim-down: reasoning_engine

G-RC-4 was signed; this phase rewrites the 8725 LOC ``core/reasoning_engine.py`` god-class.
Per continuation plan section 6 the rewrite extracts ~6-8 module-level guards/helpers into
``runtime/state_graph/guards/*`` and ``agent/reasoning_nodes/*``, then implements a real
``agent/reasoning.py`` (~580 LOC) composing those helpers plus the legacy class for the
long-tail Decision cascade; ``core/reasoning_engine.py`` collapses to a thin lazy shim.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `7f094936` | P-RC-5 P5.0a | chore(revamp): bump ledger to P-RC-5 + clean P-RC-4 ledger residue (N8) | +12 / -3 (ledger header + dup row + orphan fragment + section preface) | 0 | — |
| `26cee0e7` | P-RC-5 P5.0b | chore(core/brain): add legacy-private fallback to shim (N7) | +5 / -1 (shim docstring + 4-line fallback block; net stays 25 LOC <= baseline 26) | 0 | ADR-0001, ADR-0003 |
| `5906b606` | P-RC-5 P5.0c | test(parity): exercise runtime/llm/failover.py through real Brain (N6) | +85 / -1 (test_brain_parity.py +N6 fixture + stub LLMClient + endpoint-info parity) | +1 (test_failover_endpoint_info_parity) | ADR-0001, ADR-0003 |
| `44bbb7cb` | P-RC-5 P5.1 | refactor(runtime): convert state_graph.py to package + scaffold guards/ + reasoning_nodes/ | +43 / -2 (state_graph -> package; new guards/__init__ + agent/reasoning_nodes/__init__ docstrings) | 0 | ADR-0002, ADR-0007 |
| `dd580538` | P-RC-5 P5.2 | refactor(runtime/state_graph/guards): extract source-tag consistency + regex patterns | +180 (_text_patterns.py + source_tag.py + 19 new tests) / -49 (legacy bodies replaced by 3 re-imports); core/reasoning_engine 8725 -> 8676; baseline rebased | +19 (5 text_patterns + 14 source_tag incl. 10 parity cases) | ADR-0001, ADR-0002 |
| `80412fd5` | P-RC-5 P5.3a | refactor(runtime/state_graph/guards): extract tool-failure ack + successful-tool aggregator | +175 (tool_failure_ack.py) / -107 (4 legacy bodies replaced with re-imports); core/reasoning_engine 8676 -> 8565; baseline rebased | 0 (tests follow in P5.3b) | ADR-0001, ADR-0002 |
| `b0160926` | P-RC-5 P5.3b | test(runtime/state_graph/guards): 20 cases for tool-failure ack + successful-tool aggregator | +133 (test_tool_failure_ack.py) | +20 (8 ack parity + 5 ack negative + 1 banner shape + 1 word-list anchors + 5 successful-tool + 1 successful-tool parity) | ADR-0001 |
| `718be8b5` | P-RC-5 P5.4 | refactor(runtime/state_graph/guards): extract recap-context detector | +47 (recap_context.py) / -29 (legacy regex + fn replaced); core/reasoning_engine 8565 -> 8536; baseline rebased | +11 (recap_re anchors + 6 cases incl. 5 legacy-parity) | ADR-0001, ADR-0002 |
| `108f4843` | P-RC-5 P5.5 | refactor(runtime/state_graph/guards): extract verb-to-tool fragment maps | +85 (_verb_tool_map.py + 5 tests) / -52 (2 legacy dicts replaced by re-imports); core/reasoning_engine 8536 -> 8484 | +5 (anchor entries + shape + parity-via-identity) | ADR-0001, ADR-0002 |
| `5cc10705` | P-RC-5 P5.6a | refactor(runtime/state_graph/guards): extract unbacked-action-claim guard | +146 (unbacked_action.py) / -133 (3 legacy bodies replaced by 1 re-import); core/reasoning_engine 8484 -> 8360; baseline rebased | 0 (tests follow in P5.6b) | ADR-0001, ADR-0002 |
| `73ef0867` | P-RC-5 P5.6b | test(runtime/state_graph/guards): 17 cases for unbacked-action-claim guard | +144 (test_unbacked_action.py) | +17 (2 action_claim_re + 4 extract behaviour + 5 guard behaviour + 5 guard parity + 1 extract parity) | ADR-0001 |
| `7fd1a349` | P-RC-5 P5.7a | refactor(runtime/state_graph/guards): extract conversation-state guards | +180 (conversation_state.py) / -135 (6 legacy symbols replaced by 1 re-import); core/reasoning_engine 8360 -> 8237; baseline rebased | 0 (tests follow in P5.7b) | ADR-0001, ADR-0002 |
| `c68961c6` | P-RC-5 P5.7b | test(runtime/state_graph/guards): 21 cases for conversation-state guards | +108 (test_conversation_state.py) | +21 (1 anchor + 4 looks behaviour + 6 looks parity + 4 recoverable behaviour + 6 recoverable parity) | ADR-0001 |
| `ef74fb34` | P-RC-5 P5.8a | refactor(runtime/state_graph/guards): extract tool-filter guards | +234 (tool_filters.py) / -195 (7 helpers/constants replaced by 1 re-import block); core/reasoning_engine 8237 -> 8057; baseline rebased | 0 (tests follow in P5.8b) | ADR-0001, ADR-0002 |
| `e414a452` | P-RC-5 P5.8b | test(runtime/state_graph/guards): 22 cases for tool-filter guards | +144 (test_tool_filters.py) | +22 (anchor + 11 behaviour + 8 shell-write parametrize + 2 legacy-identity parity) | ADR-0001 |
| `8e187b8d` | P-RC-5 P5.9a | refactor(core): rename reasoning_engine.py to _reasoning_engine_legacy.py (pre-shim move) | 0 net (pure git mv, --diff-filter=R100) | 0 (tests transiently fail until P5.9b shim) | ADR-0001 |
| `a8c8509d` | P-RC-5 P5.9b | refactor(core): replace core/reasoning_engine.py body with thin shim | +25 (new shim) / 0 deletions; baseline core/reasoning_engine 8057 -> 25, adds _reasoning_engine_legacy 8057 | 0 (legacy tests resume passing) | ADR-0001, ADR-0002 |
| `d8bb22d8` | P-RC-5 P5.10 | feat(agent): implement real agent/reasoning.py on StateGraph + guard composition | +315 / -55 (real ReasoningEngine subclassing _LegacyReasoningEngine; sentinel removed; core/reasoning_engine shim repointed); agent/reasoning 51 -> 333 baseline rebased | 0 net new tests (parity lands in P5.11); existing 1078 still pass; sentinel SLOC 222 (>200 floor) | ADR-0001, ADR-0002, ADR-0003 |
| `a37a71f7` | P-RC-5 P5.11 | test(parity): 23 cases for ReasoningEngine v1/v2 parity + 10 fixtures | +180 (test_reasoning_parity.py) / 10 new JSON fixtures (auto-gen, skipped by guard) / agent/reasoning fine-tunes for guard signature mismatch | +23 (3 structural parity + 10 routing parity + 10 guard parity); pytest 1078 -> 1101 | ADR-0001, ADR-0002, ADR-0003 |
| _this commit_ | P-RC-5 P5.12 | docs(revamp): G-RC-5 gate review + STATUS scoreboard | +250 (G-RC-5.md) / +1 (STATUS scoreboard row) | 0 (docs only) | ADR-0001 |

## P-RC-6 — Phase 2 real slim-down: agent.py

G-RC-5 was signed; this phase rewrites the 9602 LOC ``core/agent.py``
god-class. Per continuation plan section 7 the rewrite extracts module-
level helpers (desktop attachment routing, destructive-intent classifier,
risk authorization replay, intent text classifiers) into ``runtime/desktop/*``
and ``agent/safety/*``, then implements a real ``agent/core.py`` (<=500 LOC)
composing those helpers plus the legacy class for the long-tail Agent surface;
``core/agent.py`` collapses to a thin lazy shim. This phase also lands four
P-RC-5 audit-nit fixes (N9 non-trivial reasoning fixtures, N10 parity
diff-test sanity wrapper, N11 legacy-file LOC audit visibility, N12
commit_guard WARN/REJECT documentation).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `be9c1b13` | P-RC-6 P6.0a | chore(revamp): bump ledger to P-RC-6 + close N11 (legacy LOC visibility) and N12 (commit_guard docs) | +60 / -7 (TRACKED_FILES + INFO_ONLY_FILES + commit_guard docstring + baseline cleanup + ledger header + section + Discipline N12) | 0 | --- |
| `89038ecc` | P-RC-6 P6.0b | test(parity): 5 non-trivial reasoning fixtures + non-triviality structural assertion (N9) | +136 (5 JSON fixtures + structural test) | +11 (10 parity + 1 structural) | --- |
| `89ddd95f` | P-RC-6 P6.0c | test(parity): diff-test sanity wrapper proving parity infrastructure catches divergence (N10) | +135 (new test_parity_diffability.py with 3 xfail-strict cases) | +3 (xfailed) | --- |
| `d64ed7df` | P-RC-6 P6.1a | refactor(runtime/desktop): scaffold runtime/desktop package + attachments helpers (extracted from core.agent) | +284 (new runtime/desktop/__init__.py + attachments.py) | 0 (tests follow in P6.1c) | ADR-0002, ADR-0003 |
| `e6596734` | P-RC-6 P6.1b | refactor(core/agent): delegate attachment helpers to runtime.desktop.attachments | +15 (alias block) / -184 (legacy helper bodies); core/agent.py 9602 -> 9433; baseline rebased | 0 | ADR-0002, ADR-0003 |
| `c3b56bce` | P-RC-6 P6.1c | test(runtime/desktop): 12 cases for attachment helpers | +186 (new tests/runtime/test_desktop_attachments.py) | +12 | ADR-0002, ADR-0003 |
| `c6b45867` | P-RC-6 P6.2a | refactor(agent/safety): scaffold agent/safety package + destructive-intent classifier (extracted from core.agent) | +355 (new agent/safety/__init__.py + destructive_intent.py with 7 helpers) | 0 (tests follow in P6.2c) | ADR-0002, ADR-0003 |
| `0b31a07d` | P-RC-6 P6.2b | refactor(core/agent): delegate destructive-intent gate to agent.safety.destructive_intent | +26 (alias block + ruff isort) / -250 (legacy bodies + redundant imports); core/agent.py 9433 -> 9208; baseline rebased | 0 | ADR-0002, ADR-0003 |
| _this commit_ | P-RC-6 P6.2c | test(agent/safety): 14 cases for destructive-intent helpers | +149 (new tests/agent/test_safety_destructive_intent.py) | +14 | ADR-0002, ADR-0003 |

## P-RC-7 — Caller migration + legacy bulk delete

_Not started._

## P-RC-8 — Endgame (renames, docs, acceptance, release)

_Not started._

## Discipline reminders (auto-collected by audits)

These are nits / gotchas the per-phase audits surfaced; they are
documented here so future executors do not re-discover them the hard
way. Each entry is one line; the audit that raised it is named in
parentheses. Once resolved across two consecutive phases, an entry
may be retired from this list.

* **N3** (G-RC-1 P-RC-1 audit): when a commit's ``Files:`` footer says
  ``PROGRESS_LEDGER.md (append ...)``, the ledger row MUST be in the
  same commit -- ``git add docs/revamp/PROGRESS_LEDGER.md`` before
  ``git commit``. No more "next commit fills the prior hash".
* **N4** (G-RC-1 P-RC-1 audit): keep per-commit diff strictly under
  400 LOC. If insertions reach 380+, STOP and split the commit
  before recording it.
* **N5** (G-RC-1 P-RC-1 audit, cosmetic): write commit messages via
  Python (``pathlib.Path("commit_msg.tmp").write_text(msg,
  encoding='utf-8')`` then ``git commit -F commit_msg.tmp``); never
  via PowerShell ``Out-File -Encoding utf8`` -- the latter prepends a
  UTF-8 BOM and the resulting commit subject reads as
  ``\ufefffeat(...): ...``.

* **T1** (G-RC-2 P-RC-2 audit, tightened): before every `git commit` on `revamp/v2`, run `python scripts/revamp_commit_guard.py --staged`. The script
  reads `git diff --cached --numstat`, skips auto-generated
  files (`package-lock.json`, `*.lock`, `*.svg`,
  `docs/revamp/*.json` baselines), warns at >= 380 hand-written
  LOC, and exits 1 at >= 400. No git hook is installed; this is
  a manual operator discipline so a legitimate one-off giant
  commit can still be force-recorded with eyes open.

* **P4-D1** (continuation plan §5, P-RC-4 entry): P-RC-4 is the first
  *real* rewrite phase. Every commit MUST grow `agent/*.py` net SLOC
  (the rewrite target) and (eventually) shrink `core/*.py` net SLOC.
  `LOC_BASELINE.json` is updated *in the same commit* that lands the
  shrink so `scripts/revamp_loc_audit.py` stays exit 0; the agent
  growth budget (`+50`) stays as-is, but per-file baselines for the
  three rewrite targets are rebased downward in the shrink commit.
* **P4-D2** (continuation plan §5.3, gate criteria): the three
  facade sentinels in `agent/brain.py` / `agent/tools.py` /
  `agent/context.py` must be REMOVED (not re-targeted) by the end of
  P-RC-4; `test_facade_files_either_declare_sentinel_or_have_real_body`
  then falls back to the 200 SLOC floor for those three files.

* **N12** (G-RC-5 P-RC-5 audit, clarification): `scripts/revamp_commit_guard.py`
  enforces two thresholds tightened from continuation plan section 0.4:
  **380 LOC = WARN** (script prints `WARN: ...` and exits 0, you may
  proceed but should stop and consider splitting); **400 LOC = REJECT**
  (script prints `REJECT: ...` and exits 1, block the commit and split
  before recording). Earlier mentions of `380 LOC` as a single cap in
  the gate notes were imprecise; the two-threshold behaviour is the
  source of truth and is encoded in both the script docstring and here.
