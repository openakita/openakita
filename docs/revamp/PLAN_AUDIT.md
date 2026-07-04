# Plan Audit — gap analysis vs `openakita_full_backend_revamp_e6d8610d.plan.md`

This audit cross-checks every line item in the original plan against
the actual `revamp/v2` branch as of commit `eaec683f`. It is the
authoritative record of where we are honest about the plan and where
we have deviated.

The plan's nominal phase ordering is **0 → 1 → 2 → 3 → 4 → 5 → 6 →
7 → 8**. In practice, after Phases 0 and 1 we jumped sideways into
the right half of the dependency graph (Phase 4 nodes → Phase 5
templates → Phase 6 templates facade), leaving Phase 2 (the
8 433 + 7 987-line agent rewrite) and one Phase 3 module
(`state_graph.py`) outstanding. This audit owns that decision and
records the consequences.

## 1. Phase-by-phase status

### Phase 0 — ADRs ✅
All ten ADRs exist under `docs/adr/0001-...md` through
`docs/adr/0010-...md`, each in its own commit. Status flag:
`Proposed` (G0 still nominally pending user signoff, but the code
has continued under the assumption that the ADRs are current).

### Phase 1 — Foundation ✅
Every Phase 1 module is in place:
`runtime/{models,checkpoint,stream,cancel_token,retry_policy,event_store,backends/sqlite,backends/json_file}.py`,
each with tests. Plan said ~12 commits; we landed in fewer because
several closely-related modules were combined. Quality of the
deliverable is unchanged.

### Phase 2 — Agent core ⚠️ **UNDERWEIGHT**
**Done**: `src/openakita/agent/state.py` (minimal `TaskState` /
`AgentState`).

**Not done** (plan called for ~14 commits):
- `agent/core.py` — port of `core/agent.py` (8 433 lines → ~500).
- `agent/reasoning.py` — port of `core/reasoning_engine.py`
  (7 987 lines → ~600). Plan says reuse LangGraph prebuilt ReAct
  shape on top of `runtime/state_graph.py`.
- `agent/brain.py` — port of `core/brain.py` (1 698 → ~400).
- `agent/tools.py` — port of `core/tool_executor.py` (1 609 → ~300).
- `agent/context.py` — port of `core/context_manager.py`
  (1 569 → ~400).
- `agent/identity.py`, `agent/permission.py`, `agent/audit.py`,
  `agent/output_guard.py`, `agent/prompt.py`, `agent/facade.py`.
- `tests/parity/` — 30-case parity harness (the G2 judgement criterion).
- `docs/revamp/core_audit.md` — plan §8 explicitly requires a
  per-file "keep-vs-rewrite" audit at Phase 2 entry. It does not
  exist.

**Why we deferred it**: the two giants are >16 000 lines combined
and need a parity harness *and* a working `runtime/state_graph.py`
underneath them. Building the right half of the dependency graph
first (nodes / templates / API facade) means the parity harness
can target a complete v2 stack instead of a half-built one. The
risk we have to accept is that G2 cannot be claimed until Phase 2
actually lands.

### Phase 3 — Runtime core ⚠️ **ONE MODULE MISSING**
**Done**:
- `runtime/ledger.py` (TaskLedger + ProgressLedger + JSON parser).
- `runtime/stall_detector.py` (regen + replan threshold).
- `runtime/supervisor.py` (dual-ledger Magentic-One end-to-end).
- `runtime/messenger.py` (clean rebuild of `orgs/messenger.py`).
- `runtime/guardrail/{runner,builtin}.py` (CrewAI-style validators
  with auto-retry).

**Not done**:
- `runtime/state_graph.py` — the "explicit StateGraph + BSP
  superstep + apply_writes (LangGraph Pregel-inspired)" engine.
  This is the missing piece behind `ConditionNode.next_address`:
  the conditional edge is evaluated and the field is populated, but
  no engine consumes it today, so multi-branch flows in v2 are
  effectively single-flight. The Phase 4 `test_node_integration.py`
  smoke avoids this case.

**G3 status**: code-level smoke is green (3-node org runs end-to-end
under the existing supervisor). The original plan's intent for G3
was a state-graph-backed BSP run; our smoke uses linear delegation
via the messenger. We documented this gap in `docs/revamp/STATUS.md`
but it remains real.

### Phase 4 — Node types ⚠️ **MANIFEST DISCOVERY MISSING**
**Done**:
- All six node modules (`base`, `tool_node`, `llm_node`,
  `condition_node`, `human_review_node`, `workbench_node`) plus
  `manifest.py`.
- `plugins/happyhorse-video/plugin.py` declares the v2 `WORKBENCH`
  constant.
- End-to-end smoke (`test_node_integration.py`).

**Not done**:
- `plugins/manager.py` was supposed to "learn to read the new
  WORKBENCH manifest; existing plugins without it keep working as
  plain tool providers." A `rg WORKBENCH src/openakita/plugins/`
  returns zero hits. Right now `WorkbenchManifest.from_dict(...)`
  is the only consumer, and it has to be invoked manually
  (templates do this; `plugins/manager.py` does not).

  Concretely this means: when `plugins.manager` finishes loading
  `happyhorse-video`, the runtime cannot ask "give me the v2
  workbench manifest you found on this plugin" — the loader-side
  half of the C2 contract is unbuilt.

### Phase 5 — Templates ✅ (with one factual correction to the plan)
**Done**: `schema.py`, `registry.py`, four built-in template
modules under `builtin/`, the discovery test, and the survivable
`@template` factory marker.

**Plan correction**: the plan §Phase 5 list specifically names
`customer_service.py` and `research_team.py` as templates to port.
**These do not exist in legacy code.** `src/openakita/orgs/templates.py`
declares exactly four dicts: `STARTUP_COMPANY`, `SOFTWARE_TEAM`,
`CONTENT_OPS`, `AIGC_VIDEO_STUDIO`. We ported all four. The plan's
naming list was aspirational; what actually shipped matches the
legacy reality 1-to-1.

**Plan inheritance**: `src/openakita/orgs/plugin_workbench_templates.py`
(225 lines) is a *runtime function* that builds workbench-as-node
templates from the currently loaded plugin set — it is not a static
template registry entry. Its v2 home should be either
`runtime/templates/dynamic.py` or a Phase 6 `api/routes/orgs_v2.py`
endpoint that calls into the live `PluginManager`. We have **not**
yet ported it; we should add an explicit todo.

### Phase 6 — API & channels ⚠️ **PARTIAL**
**Done**:
- `api/routes/orgs_v2.py` (templates list / get / instantiate).
- `api/server.py` mount.
- `settings.runtime_v2_enabled` flag wired in.
- 15 route-level tests + 3 registry-marker tests.

**Not done**:
- `runtime/facade.py` — plan §4 module tree explicitly lists this
  as "public API kept stable for api/routes consumers." We
  short-circuited it by importing `GLOBAL_REGISTRY` directly inside
  the route module. For Phase 7 cutover we should still abstract
  through a facade so a v1-shaped route can be served by v2 data.
- `channels/gateway.py` is unchanged. Per-org runtime selection
  (the headline of Phase 6) does not yet exist.
- `apps/setup-center/src/components/OrgChatPanel.tsx` v2 stream
  subscription + progress-ledger timeline rendering are not done.
- v2 OrgV2 persistence resource (`POST /api/v2/orgs`) — overlaps
  with Phase 7.

### Phase 7 — Migration & cutover ⏸ Pending (correct).

### Phase 8 — Cleanup ⏸ Pending (correct).

## 2. Cross-cutting gaps

- **`docs/revamp/gates/Gxx.md`** — plan §6 requires "Each gate
  produces a written review note ... (one commit each)". The
  folder does not exist. This is purely paperwork: the technical
  gates G1, G3 (with the state_graph caveat), G4 (with the
  manifest-loader caveat), and G5 are all met; gate notes need to
  be authored and committed.
- **G0 ADR signoff** — every ADR is still `Status: Proposed`. The
  user-led ADR review has not happened in writing. Code has
  continued under the assumption that the ADRs are current; this
  is a real risk only if a substantial design point comes back up
  for revision.

## 3. Recommended order to close gaps (P0 → P2)

### P0 — closes real holes in already-claimed gates

1. **`runtime/state_graph.py`** — small, focused module
   (~300-400 lines). Writes a Pregel-style superstep + writes
   apply pass that consumes `DelegationResult.next_address` as the
   primary speaker selection signal. Updates `Supervisor` to route
   through the graph when the org defines conditional edges. Closes
   the lingering G3 caveat and unblocks the G4 manifest-driven
   end-to-end test.
2. **`plugins/manager.py` WORKBENCH discovery** — small extension
   (~80-150 lines). Read the `WORKBENCH` module-level constant on
   each loaded plugin, validate via `WorkbenchManifest.from_dict`,
   and expose it through `loaded_plugins[id].workbench_manifest`.
   Plugins without the constant remain plain tool providers; that
   path is already exercised by the existing test suite.

### P1 — needed for G2

3. **`docs/revamp/core_audit.md`** — produce the per-file audit
   plan §8 mandates. Mark each `core/*.py` as
   keep-as-is / move / rewrite. This is the cheapest commit that
   unblocks Phase 2.
4. **`agent/identity.py` / `agent/permission.py` / `agent/audit.py` /
   `agent/output_guard.py`** — the small, well-scoped modules. Plan
   §8 expects most of these to be moved largely as-is. Each is one
   commit ≤ 200-300 lines.
5. **`agent/prompt.py`** — small, port from `core/prompt_assembler.py`.
6. **`agent/context.py`** — medium-large; depends on a parity case
   to anchor behaviour, so prep `tests/parity/` shell first.
7. **`agent/brain.py` + `agent/tools.py`** — medium. Build on top
   of the foundation modules.
8. **`agent/reasoning.py` + `agent/core.py`** — the giants. Drive
   them through the `runtime/state_graph.py` engine landed in P0.
9. **`tests/parity/`** — start small (5 cases), add one per
   reasoning slice ported. G2 needs ≥95 % match on 30 cases.

### P2 — paperwork / nice-to-have

10. **`runtime/facade.py`** — extract a public API surface so
    `api/routes/*` can switch on the flag without touching v2
    internals.
11. **`docs/revamp/gates/G1.md` … `G6.md`** — gate review notes,
    one commit each.
12. **`runtime/templates/dynamic.py`** — port of the
    `orgs/plugin_workbench_templates.py` runtime template generator.

## 4. Conclusion

The revamp is **on plan in scope but off plan in sequencing**: we
have completed the right half of the dependency DAG (nodes →
templates → templates API) before the left half (state graph →
agent rewrite). Catching up means executing P0 immediately to
close the lingering caveats in already-shipped gates, then
sequentially walking the Phase 2 module list with a parity
harness in lock step. None of the deviations contradict the plan;
they only mean the gate sign-offs cannot be claimed cleanly until
P0 + P1 land.
