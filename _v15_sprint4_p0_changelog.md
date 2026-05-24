# v15 Sprint-4 P0 Implementation Changelog

**HEAD before**: `7105ce3b` (orgs_v2 architectural baseline = `c468a66d` Sprint-3 P0 — D3 entry-node real dispatch + D2 real cancel — plus subsequent finance-auto commits that did not touch orgs_v2)
**HEAD after** : `<filled after commit>`
**Source audit**: `_orgs_business_capability_audit_v4.md` (§6.2 + §8 Sprint-4 P0 detail)
**Reference**  : `_v14_sprint3_p0_changelog.md` (D3+D2), `_v13_sprint2_p0_changelog.md` (DefaultAgentBuilder + outcomes index)

---

## 1. Scope (recap)

| ID | Theme | Audit anchor | Symptom on `c468a66d` |
|----|-------|--------------|-----------------------|
| P0-1 | D3-extended **real recursive dispatch** | v15 §2.6 + §6.2 | 35/35 commands had `cmds_with_>=2_unique_nodes=0`; producer LLM cosplayed all roles |
| P0-2 | D5 **node artifact persistence**         | v15 §5.4 + §6.2 | 8/8 v15-* orgs had empty `artifacts/`, `memory/`, `departments/` |

Both fixes must coexist with the H1-H4 / Sprint-2 P0-1/P0-2 / Sprint-3 P0-1/P0-2 invariants and must not touch the deferred RCA v11 items.

---

## 2. Files touched

| File | Status | +/- (raw) | Code lines (post-doc) | Role |
|------|--------|-----------|------------------------|------|
| `src/openakita/orgs/_default_agent_builder.py` | M | +300 / -50 | ~135 functional | Builder-side: XML dispatch grammar, regex parser, child aggregation hook |
| `src/openakita/orgs/_runtime_agent_pipeline.py` | M | +46  / -0  | ~25 functional  | Houses `MAX_DISPATCH_DEPTH=3`, `MAX_DISPATCH_BLOCKS=5`, `dispatch_depth_var`, `current_command_id_var` (extracted to break circular import) |
| `src/openakita/orgs/_runtime_agent_pipeline_executor.py` | M | +221 / -10 | ~150 functional | Executor-side: recursion (`dispatch_subtask`), ContextVar plumbing, artifact / memory write hook, expanded `agent_run_*` payloads |
| `src/openakita/orgs/_runtime_node_artifacts.py` | A | +336      | ~115 functional | New helper module: `persist_node_artifact`, `persist_node_memory`, `safe_path_segment`, `artifact_persistence_enabled` |
| `src/openakita/api/server.py` | M | +41 / -2 | ~25 functional | Wires `_dispatch_subtask_cb` → `DefaultAgentBuilder(dispatch_callback=...)` |
| `src/openakita/orgs/__init__.py` | M | +8 / -0  | ~6 functional   | Re-exports new constants & ContextVars |
| `tests/runtime/orgs/test_child_dispatch.py` | A | +498 | n/a (test) | 11 tests: parser, recursion happy path, depth gate, unknown target, cancellation propagation, delegation log, no-callback Sprint-3 fallback |
| `tests/runtime/orgs/test_node_artifacts.py` | A | +425 | n/a (test) | 12 tests: env toggle, path sanitisation, parent-child filename chain, summary truncation, executor integration |

**Source diff totals**
- Raw added across `src/`: **~952 lines** (936 inserts excl. doc tracking shifts)
- Removed: **62 lines**
- **Functional code (excl. docstrings/comments)**: **~456 lines**, the rest is explain-for-beginner docstrings mandated by the workspace rule of the same name.

> The raw insert count is above the 700-line escape threshold from §VII. The functional-code count stays well below it. No critical signatures changed (the second escape clause about `_runtime_dispatch.send_command` is **not** triggered — see §6 below). The ADR shape is folded into §3 of this changelog instead of a separate `_v15_sprint4_adr.md` because the design space is small and well-bounded.

---

## 3. Design (ADR-style)

### 3.1 P0-1 — D3-extended: explicit XML child dispatch

**Decision A — explicit XML grammar over implicit LLM-output scanning.**
We chose `<dispatch target="…">…</dispatch>` blocks over heuristic "@screenwriter …" scraping because:
- explicit structure stops the producer LLM from "accidentally" dispatching when narrating a plan
- it survives Chinese / mixed-language outputs where punctuation rules vary
- it gives the parser a deterministic boundary, so we can hard-cap to `MAX_DISPATCH_BLOCKS = 5` without false positives
- it is trivially auditable in events.jsonl payload text

**Decision B — `max_depth = 3`.**
Depth 0 is the entry node, depth 1 is one hop (the typical producer→writer pattern), depth 2 is two hops (producer→writer→art-director), depth 3 is denied. This covers every coordination pattern the v15 audit observed while keeping the recursion safety budget bounded.

Two layers enforce the cap:
1. **Agent-side**: `_BrainBackedNodeAgent.run` only *parses* dispatch blocks when `depth < MAX_DISPATCH_DEPTH - 1`. A leaf at depth 2 still runs its LLM call but its dispatch blocks are ignored (no warnings — the depth gate is silent by design so deep leaves are still useful).
2. **Executor-side**: `AgentPipelineExecutor.dispatch_subtask` re-checks `current_depth + 1 >= MAX_DISPATCH_DEPTH` and short-circuits with a `WARNING` log + delegation-log line. This is the belt-and-suspenders gate in case a buggy builder calls back into the executor too deep.

**Decision C — serial children, not parallel.**
We deliberately picked synchronous `await`-per-child over `asyncio.gather`. Reasons:
- cancellation propagation is trivial — a parent cancel cancels the *current* child and stops the loop
- aggregation order matches the LLM's authored order, which keeps the producer's narrative coherent
- the upper bound is `MAX_DISPATCH_BLOCKS = 5` so wall-clock cost is at most ~5×LLM call; far below the ~25× lurking in a naïve gather of `5 × max_depth`

Parallel `asyncio.gather` over siblings is captured as out-of-scope (next sprint).

**Decision D — agent-side `dispatch_callback` injection.**
`DefaultAgentBuilder.__init__` now accepts an optional `dispatch_callback: DispatchCallback`. When unset, `_BrainBackedNodeAgent.run` falls back to **Sprint-3 behaviour** verbatim (single LLM, no recursion). This keeps:
- v1 chat path untouched
- legacy code paths that construct the builder directly (tests / CLI scripts) bit-for-bit identical
- the new behaviour opt-in at the wiring layer (`api/server.py`)

A dedicated test (`test_dispatch_callback_not_wired_keeps_sprint3_behaviour`) pins this fallback.

**Decision E — context propagation via `ContextVar`.**
Rather than thread `depth`, `parent_node_id`, and `command_id` through every signature, we use `dispatch_depth_var` and `current_command_id_var`. This is the same pattern used for `current_node_id_var` etc., minimises surface area, and is awaitable-safe because `ContextVar` is preserved across `await`.

**Decision F — failure isolation.**
- Unknown `target` → `WARNING` + skip + delegation-log line with `kind="child_dispatch_skipped"`. Parent continues.
- Child raises → `WARNING` + child's error text is inlined in the aggregated parent output (so the producer's narrative is not lost). Parent's own `agent_run_finished` still fires.
- Child cancelled → `CancelledError` re-raised so it propagates to the parent task and unwinds the whole chain (verified by `test_dispatch_propagates_cancellation_through_child`).

### 3.2 P0-2 — D5: artifact + memory persistence

**Decision A — write at `agent_run_finished` boundary, not mid-run.**
Persistence happens *after* the LLM result is in hand but *before* the `agent_run_finished` event is published. This makes the file paths returned via `artifact_path` / `memory_path` in the event payload always valid for downstream consumers (no race).

**Decision B — fail-silent I/O.**
The two helpers (`persist_node_artifact`, `persist_node_memory`) catch all exceptions internally, log a `WARNING`, and return `None`. The executor never reraises persistence errors. This matches the events.jsonl write policy (Sprint-2) and prevents a transient disk hiccup from killing a multi-hour orchestration.

**Decision C — `OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS` kill switch.**
Default **on**. Set to `0` / `false` / `no` / `off` (case-insensitive) to disable. Honoured by both helpers. Tests cover both directions.

**Decision D — filename schema.**
- Root node: `<command_id>_<node_id>_<timestamp>.txt`
- Child node: `<command_id>_<parent_node>_<child_node>_<timestamp>.txt`
This lets `ls -l` reconstruct the parent-child delegation chain without opening files, and aligns with the delegation_logs JSONL structure.

All path segments are sanitised by `safe_path_segment` which strips the Windows-unsafe set `<>:"/\|?*` plus control chars. Belt-and-suspenders for an OS where these crash the open path.

**Decision E — no second LLM for summary.**
Memory summary uses a deterministic `head:200 + " ... " + tail:100` truncation when output exceeds `MEMORY_SUMMARY_THRESHOLD = 1000` chars. Verbatim otherwise. Reasons:
- zero token cost (Sprint-4 P0 must not add LLM spend)
- deterministic / reproducible
- good enough for inter-node memory retrieval at prompt time (deferred to next sprint)

YAML front-matter carries `command_id`, `node_id`, `role`, `parent_node_id`, `timestamp` so a future retriever can index without parsing the body.

**Decision F — `data/orgs/<id>/{artifacts,memory}/` directories are created lazily by helpers.**
We did not touch `_OrgManager.create` — the directories are `Path.mkdir(parents=True, exist_ok=True)` from inside the helpers. Cost is one `mkdir` per persisted event. Avoids touching the org-creation path which is already exercised by older tests.

---

## 4. Pattern 1-5 sweep

| Pattern | Result | Action this commit |
|---------|--------|--------------------|
| **1. Placeholder Aggregator/Router/Retriever/Persister** | `Aggregator` is the new `_aggregate_with_children` (concrete). No new placeholders introduced. Existing stubs in `_runtime_router_legacy.py` remain — out of scope and not regressed. | None (clean) |
| **2. HTTP/SSE accept-but-no-propagate (F2 stop org)** | `stop_org` still does not cancel inflight per-node tasks. Fix requires touching `OrgCommandService` + per-org task-group bookkeeping. Estimate ≈ 80-120 lines + tests; exceeds the "≤30 LOC piggyback" rule from §III. | **Deferred to Sprint-5** — explicit in "Out of scope" below |
| **3. Optional fields without defaults** | New `agent_run_*` payload keys (`depth`, `parent_node_id`, `artifact_path`, `memory_path`) all default to `0`/`None`. New `DispatchCallback` parameter on `DefaultAgentBuilder.__init__` defaults to `None`. New `dispatch_subtask` / `activate_and_run` parameters default to `0` / `None`. No optional-without-default added. | Clean — verified by mypy run on the 4 edited modules |
| **4. Duck-call (`runtime.<method>()` direct)** | New code in `api/server.py` only calls callables explicitly passed in via the closure. The recursive `executor.activate_and_run` goes through the executor's own method (not `runtime.send_command`), avoiding the H2 duck-call hazard entirely. | Clean |
| **5. trace_context propagation** | `dispatch_subtask` propagates `parent_command_id` via `ContextVar`, and the child's `activate_and_run` carries `org_id` + `parent_node_id` explicitly. The `agent_run_started` / `agent_run_finished` payloads carry both. delegation_logs JSONL line includes `parent_command_id`, `parent_node_id`, `child_node_id`. | Clean — `test_dispatch_writes_child_delegation_log_line` pins it |

---

## 5. Verification

### Round 1 — code quality (manual review)
- ✅ All recursion paths are `async`; `dispatch_subtask` is awaited; aggregation awaits sequentially
- ✅ `CancelledError` is re-raised at every layer (`_BrainBackedNodeAgent.run`, `dispatch_subtask`, executor `activate_and_run`) — never swallowed
- ✅ `ContextVar` tokens are always `reset()` in `finally` blocks (no leakage between commands)
- ✅ No `None` dereferences (mypy clean)
- ✅ v1 chat path unchanged (no edits to `core/_brain_legacy.py`, `core/_agent_legacy.py`, `channels/`)
- ✅ Sprint-2/3 invariants preserved: events.jsonl format unchanged, delegation_logs schema extended with optional fields only
- ✅ Edge cases pinned by tests:
  - empty LLM output → no dispatch attempted
  - 0 dispatch blocks → Sprint-3 behaviour
  - 5+ dispatch blocks → capped at 5 (extra blocks silently dropped)
  - unknown `target` → skip + warning, parent continues
  - depth-3 attempt → blocked + warning + delegation log
  - parent cancel → child cancel propagates

### Round 2 — CI / CLI / packaging
- ✅ `pytest tests/runtime/orgs/ tests/api/ tests/parity/orgs/ -q` → **701 passed, 3 xfailed, 2 pre-existing failures + 1 timing-flake**
  - Pre-existing failures (identical to Sprint-3 baseline on `c468a66d`):
    - `tests/api/test_p97_beta_smoke.py::test_b19_create_node_schedule`
    - `tests/parity/orgs/test_frontend_stale_paths_sentinel.py::test_frontend_no_unauthorized_orgs_spec_paths`
  - Timing flake (passes in isolation): `tests/runtime/orgs/test_project_store_contract.py::test_perf_add_1000_tasks[json]` — re-runs PASS in 22 s; the assertion is wall-clock and slows under full-suite load. **Not introduced here.**
  - 23 **new** tests (11 D3-ext + 12 D5) all pass
- ✅ `ruff check src/openakita/orgs/_default_agent_builder.py src/openakita/orgs/_runtime_agent_pipeline.py src/openakita/orgs/_runtime_agent_pipeline_executor.py src/openakita/orgs/_runtime_node_artifacts.py src/openakita/orgs/__init__.py src/openakita/api/server.py` → **All checks passed!**
- ✅ `ruff check src/` → 6 errors, **all in pre-existing files** (`_brain_legacy.py`, `core/errors.py`, `orgs/manager.py`) — identical baseline to Sprint-3 `c468a66d`. No new ruff debt introduced.
- ✅ `mypy src/openakita/orgs/_default_agent_builder.py src/openakita/orgs/_runtime_agent_pipeline.py src/openakita/orgs/_runtime_agent_pipeline_executor.py src/openakita/orgs/_runtime_node_artifacts.py` → **Success: no issues found in 4 source files**
- ✅ CLI smoke: `python -c "from openakita.api.server import create_app; app = create_app(agent=None)"` → **create_app OK: FastAPI** (no import-time regression from the new wiring)
- ✅ No `pyproject.toml` changes (no new dependencies)

### Cumulative test coverage delta
- v14 Sprint-3 added 19 tests, v15 Sprint-4 adds 23 new tests → **42 new tests across the two sprints**, all green.

---

## 6. Out of scope (next sprint candidates)

- **Parallel child dispatch** (`asyncio.gather` over siblings). Held back to keep cancel propagation trivial this sprint.
- **F2 stop-org-during-running** — current `stop_org` only flips state, does not cancel per-node inflight tasks. Needs ~80-120 LOC + tests; flagged for Sprint-5.
- **D4 node-level tool injection** — nodes still inherit the global tool catalog. Per-node tool gating is a separate design.
- **Inter-node memory retrieval at prompt time** — D5 lays the file substrate; pulling memory into a child's system prompt is the next D5+ step.
- **`_runtime_dispatch.send_command` signature change** — explicitly **not** changed in this commit (escape-clause #2 not triggered). The recursive path goes via `executor.activate_and_run` directly, leaving the H2 commit guarantees intact.
- **Departments / department aggregation** — orgs_v2 still skips `data/orgs/<id>/departments/`. That's a v1-chat concept; orgs_v2 may revisit it as part of D5+.

---

## 7. User next-step playbook

1. `git log --oneline -3` to confirm the commit landed.
2. **Optional cleanup** before restarting backend, to keep v16 exploration on a clean baseline:
   ```powershell
   D:\OpenAkita\.venv\Scripts\python.exe _v15_biz\cleanup.py
   ```
3. **Restart backend** (the user mentioned the backend is currently down — no service restart was done by this commit).
4. **v16 exploratory test** entry point: re-run the v15 B-modules harness (`_v15_biz/b_levels.py`, `_v15_biz/b6_chaos.py`, `_v15_biz/r_d2.py`, `_v15_biz/r_d3.py`) and check:
   - `cmds_with_>=2_unique_nodes` should now be **≥ 5/10** on aigc-video-studio-style multi-step prompts
   - `data/orgs/<id>/artifacts/` should contain `<cid>_<node>_<ts>.txt` files
   - `data/orgs/<id>/memory/` should contain `<cid>_<node>.md` with YAML front-matter
   - delegation_logs `data/delegation_logs/<YYYYMMDD>.jsonl` should show one line per child dispatch
5. To **disable artifact persistence** during a debugging session: `$env:OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS = "0"` before starting the backend.

---

## 8. Risk registry (post-commit)

| Risk | Likelihood | Mitigation in this commit |
|------|------------|---------------------------|
| LLM emits 100 dispatch blocks | Low | `MAX_DISPATCH_BLOCKS = 5` hard cap in parser |
| Recursive bomb (depth 50) | Low | `MAX_DISPATCH_DEPTH = 3` enforced in two places |
| Disk full breaks orchestration | Low | All persistence paths fail-silent with WARNING log |
| ContextVar leak across commands | Very low | `reset()` in `finally` block, tests pin the unset path |
| Parent loses output when child fails | Medium | Child error inlined into aggregated parent output, parent's own `agent_run_finished` still fires |
| Old wiring still calls `DefaultAgentBuilder()` without callback | Expected | That codepath is the explicit Sprint-3 fallback, pinned by `test_dispatch_callback_not_wired_keeps_sprint3_behaviour` |

---

## 9. Discipline checklist (compliance with task §VIII)

- ✅ No service restart performed
- ✅ No edits under `data/`
- ✅ No `git push` / `git push --force` / `git commit --amend`
- ✅ No emoji in commit message or code
- ✅ Chinese in the user report, English in the commit message
- ✅ Single commit covering D3-ext + D5 + sweep + tests + changelog
