# P-RC-9 Charter — `orgs/` integral migration (deferred from P-RC-7)

**Status: NOT EXECUTED.** This document is the deferred-work
charter for the R-RC-7-A residual the P-RC-7 gate review escalated
to P-RC-8, which P-RC-8 in turn explicitly punts to a future
P-RC-9 plan. It exists so the operator and the next plan author
can pick the work up without re-discovering the scope.

P-RC-8 is the **terminal phase of the continuation plan**; nothing
in this charter is implemented on the `v2.0.0-rc2` tag. The legacy
`src/openakita/orgs/` package (~880 KB across 26 files, 86
production import sites, 2533 LOC of v1 REST in
`api/routes/orgs.py`) is **still live** on `revamp/v2` and is the
canonical implementation operators use until P-RC-9 lands.

## Why orgs/ couldn't be deleted in P-RC-7

The P-RC-7 audit (`docs/revamp/gates/G-RC-7.md` "R-RC-7-A" section)
established the blocker: the v2 `src/openakita/runtime/orgs/`
package shipped during P-RC-3 is **storage-only** (`SqliteOrgStore`
+ `JsonOrgStore` + a shared contract suite). It has no v2
equivalents for the six subsystems v1 `orgs/` provides, so a
mechanical `git rm -r src/openakita/orgs/` would break:

* `api/routes/orgs.py` — 2300 LOC of REST endpoints that call
  `OrgManager.create()`, `OrgRuntime.dispatch()`,
  `OrgCommandService.command()`, `ProjectStore.list()`, …
* `api/server.py` — startup wiring that registers v1 routes.
* `channels/gateway.py` — IM gateway commands that call
  `OrgRuntime.dispatch_inbound_message()`.
* `_reasoning_engine_legacy.py:7908` — one residual legacy call
  site (cleaned up if the legacy file is also removed in P-RC-9).
* ~50 test files under `tests/orgs/` that import from the v1
  package.

The structural map (G-RC-7 audit, verified by `git grep "from
openakita.orgs"`):

* **63 production import sites** (in `api/`, `channels/`,
  `agents/`, `mcp_server.py`, … — every IM gateway path that
  responds to a user message hits at least one).
* **23 test import sites** (mostly `tests/orgs/*.py` with a few
  cross-cutting integration tests).

This is the same shape as the brain/reasoning/tools/context/agent
giants that P-RC-4..P-RC-7 migrated in 95 commits — except those
were medium-LOC god-classes with stable Python interfaces, and
`orgs/` is six tightly-coupled subsystems with a REST surface and
a stateful runtime.

## What v2 subsystems must be written

To delete `src/openakita/orgs/` cleanly, P-RC-9 must implement
the following six v2 subsystems under `src/openakita/runtime/orgs/`
(or fold them into the surviving `runtime/` packages):

1. **`OrgManager`** — create / update / delete / clone OrgV2; the
   API surface `api/routes/orgs.py` calls today. Must respect the
   `JsonOrgStore` / `SqliteOrgStore` contract suite the v2
   storage layer already enforces. **Estimate:** ~400-600 LOC,
   contract tests + transition tests.

2. **`OrgRuntime`** — the active-org execution shell. Wraps
   `runtime.supervisor.Supervisor` per active org, manages the
   `CancellationToken`, owns the `StreamBus` subscriptions,
   bridges to `runtime.state_graph.StateGraph`. This is the
   biggest piece; v1 has ~1000 LOC across `orgs/runtime.py` and
   `orgs/runtime_manager.py`. **Estimate:** ~800-1200 LOC + ~30
   integration tests.

3. **`OrgCommandService`** — IM-side command verb parsing and
   dispatch (`/start`, `/cancel`, `/status`, `/resume`, …). Most
   of the cancel-verb wiring landed in P-RC-1 P1.5
   (`a97fa73b`); the rest of the command surface still routes
   through v1. **Estimate:** ~300-400 LOC.

4. **`OrgBlackboard`** — shared key/value scratch space agents
   read/write during a turn. v1 has it on `OrgRuntime`; v2 should
   give it its own module so checkpoint round-trips and resume
   semantics are explicit. **Estimate:** ~200-300 LOC + contract
   tests against `runtime.checkpoint`.

5. **`ProjectStore`** — multi-org persistence layer above the
   per-org `JsonOrgStore` / `SqliteOrgStore`. Owns the project
   listing, last-active timestamps, per-org settings overrides.
   **Estimate:** ~250-400 LOC.

6. **`NodeScheduler`** — node-level scheduling for the v2 active
   `StateGraph`: which node runs next, which is paused awaiting
   human review, which is delegating. Probably folds into
   `runtime.messenger` + `runtime.state_graph` rather than being
   its own module, but the v1 logic needs a v2 home that isn't
   `_reasoning_engine_legacy`. **Estimate:** ~300-400 LOC.

Other supporting surfaces v2 already has and that P-RC-9 will
**re-use, not rewrite**:

* `runtime.checkpoint` + `runtime.backends.{sqlite,json_file}` —
  storage contract closed at P-RC-3.
* `runtime.stream` + `StreamBus` — channels, close gate, idle
  cleanup all green.
* `runtime.supervisor` + `runtime.ledger` + `runtime.stall_detector`
  — the inner orchestration loop is done.
* `runtime.state_graph` — the routing engine for the new
  `NodeScheduler` to feed.
* `runtime.templates` — discovery + instantiate is the v2 entry
  point for new orgs.
* `runtime.nodes.{tool,llm,condition,human_review,workbench}` —
  every node type the new `OrgRuntime` needs.

## Estimated scope

* **Calendar:** 4-6 weeks (one engineer full-time on the v2
  subsystems + parity harness + caller migration). Wider if the
  REST surface needs an authentication or backward-compat layer.
* **Commits:** ~30-50 (mirrors the P-RC-4..P-RC-7 cadence: each
  subsystem ships as scaffold + rewire + tests + parity, plus
  a final caller-migration phase, plus the `git rm` phase).
* **Parity harness:** required (~10-20 fixtures per subsystem)
  — the v1 `orgs/` package is what production runs against today,
  so the rewrite must prove byte-faithful behaviour before the v1
  package is deleted. The P-RC-5/P-RC-6 parity-suite pattern
  applies directly: build v1-vs-v2 against recorded fixtures,
  assert results match, lock in.
* **Gate:** a separate **G-RC-9** review at the end. P-RC-8 does
  not pre-approve any P-RC-9 commits.

## Status and call to action

**Status: NOT executed in the continuation plan.** P-RC-8 (this
phase, terminal) does the docs and the v2.0.0-rc2 tag; P-RC-9 is
its own plan with its own gate. The user / operator chooses
whether and when to start it.

**Recommendation for operators running v2 today:** keep the legacy
`src/openakita/orgs/` surface live. The IM gateway, the `/api/orgs`
REST routes, and the `tests/orgs/*.py` suite all depend on it. The
v2 `runtime/` + `agent/` surfaces co-exist with `orgs/` cleanly
(only the giant `core/*.py` shims were deleted in P-RC-7).

**Suggested branch / plan layout for the next session:**

* New branch off `revamp/v2`: `revamp/v3-orgs` (or similar; `v2.x`
  patch-style if the migration ships incrementally).
* New plan file under `c:\Users\Peilong_Hong\.cursor\plans\` —
  e.g. `openakita_orgs_migration_p_rc_9_<id>.plan.md`.
* Re-use the continuation-plan template (sections 0..9,
  G-RC-9 gate at the end).
* Re-use `scripts/revamp_commit_guard.py` (380 WARN / 400 REJECT)
  and `scripts/revamp_loc_audit.py` (drop `orgs/runtime.py`,
  `orgs/tool_handler.py`, `orgs/templates.py`,
  `orgs/messenger.py` baselines down to 0 as each subsystem is
  rewritten).

## Cross-references

* `docs/revamp/gates/G-RC-7.md` "R-RC-7-A" section (the original
  scope-check that triggered this charter).
* `docs/revamp/ACCEPTANCE.md` criterion 5 (the Partial rating
  whose deferral is documented here).
* `docs/revamp/STATUS.md` scoreboard footer (P-RC-9 pointer added
  in P8.4 — see this commit).
* `docs/revamp/RELEASE_v2.md` "Deferred to P-RC-9" section
  (operator-facing release-notes pointer, added in P8.5).
