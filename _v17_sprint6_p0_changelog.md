# v17 Sprint-6 P0 Changelog

## Scope

Sprint-6 closes the three P0 gaps the v17 audit
(`_orgs_business_capability_audit_v6.md`) discovered in the Sprint-5
P0 work (`5960bf3e`). The diagnoses live in `_v17_p1_rca.md` §§1-2 +
appendix A. This changelog covers what shipped, why, what testing
proves it, and what is deferred.

* P0-1 (D4 tool execution loop closure) -- `NodeToolHost` wrapper
* P0-2 (F2 stop-org telemetry alignment) -- `cancelled_by` field on
  the on-disk `agent_run_cancelled` / `user_command_cancelled` /
  `agent_run_watchdog_killed` payloads
* P0-3 (`hh_*` plugin tool bridging) -- comes for free with P0-1's
  wrapper because plugin handlers already live on the desktop
  Agent's `handler_registry`; surfaced via a distinct
  `reason="plugin_not_loaded"` `node_tool_failed` payload

The total **production** source diff is 447 LOC (modified, 7
modules) + 350 LOC (new `_runtime_agent_host.py`, ~40% docs/comments),
under the 1000-LOC escape-hatch threshold in the Worker prompt
(`_v17_p1_rca.md` §六). No ADR required.

## Modified / new files

| File | LOC | Purpose |
| --- | --- | --- |
| `src/openakita/orgs/_runtime_agent_host.py` | +350 | NEW. `NodeToolHost` wrapper + `ToolNotAvailable`. |
| `src/openakita/orgs/_runtime_node_tools.py` | +126 / -? | Thread `tool_host` through `resolve_node_tools` + `execute_node_tool` + `run_with_tools`. Classify `ToolNotAvailable` as `plugin_not_loaded`. |
| `src/openakita/orgs/_default_agent_builder.py` | +47 | Pass `tool_host_provider` into `_BrainBackedNodeAgent`; resolve on each `run`. |
| `src/openakita/orgs/runtime.py` | +64 | `_node_tool_host` slot + `set_node_tool_host` / `get_node_tool_host`; `cancel_user_command` gets `cancel_reason` kwarg. |
| `src/openakita/orgs/_runtime_dispatch.py` | +54 | `cancel_user_command(cancel_reason=...)`; emit `cancelled_by` on `user_command_cancelled`. |
| `src/openakita/orgs/_runtime_agent_pipeline_executor.py` | +46 | `cancel_source_provider` ctor arg; `agent_run_cancelled` payload now carries `cancelled_by`. |
| `src/openakita/orgs/command_service.py` | +59 | `cancel_all_for_org(reason)` forwards to runtime via `cancel_reason`; `_watchdog_tick` emits `cancelled_by="watchdog"` on the event; new accessor `get_cancel_source(command_id)`. |
| `src/openakita/api/server.py` | +95 | `_refresh_node_tool_host` lifespan helper (called from `create_app`, `update_agent`, `update_runtime_refs`); `_orgs_v2_node_tool_host_provider` + `_orgs_v2_cancel_source_provider` closures. |
| `tests/runtime/orgs/test_node_tool_host_integration.py` | +304 | NEW. Five integration cases including real filesystem write + events.jsonl read. |
| `tests/runtime/orgs/test_cancelled_by_disk_integration.py` | +553 | NEW. Seven integration cases covering stop-org / user-cancel / watchdog -> events.jsonl. |
| `_v17_sprint6_p0_changelog.md` | NEW | This file. |

## P0-1 -- D4 NodeToolHost (RCA §1)

### Root cause (recap)

`_runtime_node_tools.execute_node_tool` called
`default_handler_registry.execute_by_tool(tool_name, args)`.
`default_handler_registry` is a *module-level singleton*
`SystemHandlerRegistry()` (see `tools/handlers/__init__.py:404`).
**Nothing in the codebase populates it** -- every
`register_handler(...)` call points at a per-Agent
`self.handler_registry` (`core/_agent_legacy.py:1099-1101 +
2216-2285`). Result: every D4 node tool call raised
`ValueError: No handler mapped for tool: <name>`, the surrounding
`except Exception` block silently turned that into
`node_tool_failed`, and v17 audit recorded
0 `node_tool_completed` for 12 `node_tool_called` (audit
§1.R.D4).

### Decision (RCA §1.5 method B + §六 escape-hatch)

The full per-org Agent instance the RCA originally proposed
costs ~500 LOC and pulls in browser / MCP / scheduler / shell
managers we do **not** need for orgs_v2 nodes (RCA §1.5.4 risk).
The Worker prompt's escape hatch explicitly authorises a minimum
wrapper when the v1 path is tightly coupled to an `Agent`
instance:

> 如果 NodeToolHost 实施时发现 v1 chat handler_registry 注册路径强依赖 Agent 实例 / 工厂 state...本次用最小 wrapper 兜底

The handlers ARE tightly coupled (`FilesystemHandler` reads
`agent.default_cwd` / `agent._execution_env_spec`,
`MemoryHandler` reads `agent.memory_manager` etc.). So Sprint-6
ships `NodeToolHost` as a **per-process wrapper** that re-uses
the main desktop `Agent.handler_registry` instead of building a
new one:

* `NodeToolHost.__init__(*, agent, org_id)` only stores
  references -- no extra handler instantiation, no MCP /
  browser bring-up.
* `execute_tool` calls `agent.handler_registry.execute_by_tool`,
  raising `ToolNotAvailable` (a `LookupError` subclass) for
  unknown tools.
* `lookup_tool_definition` scans `agent._tools` (plugin
  extensions land there via `plugins/api.py:300`) first, then
  falls back to `tools/definitions/get_tool_definition`. This
  is what closes P0-3 transparently (`hh_*` tools become
  discoverable by `resolve_node_tools`).
* `dispose()` drops the agent reference for clean rebinds on
  hot reload.

### Wiring (api/server.py)

`_refresh_node_tool_host(app)` is idempotent and is called from:
1. `create_app` initial lifespan, after `org_runtime` is created.
2. `update_agent(app, agent)` (called by `main.py` after the
   desktop Agent finishes async init).
3. `update_runtime_refs(app, ...)` (IM gateway late bind).

When the desktop Agent is not yet ready (lifespan-race), the
host is `None` and `execute_node_tool` falls back to the
Sprint-5 `default_handler_registry` path -- v17 observable
preserved for the test fixtures that monkey-patch the global
(RCA §1.5.4 rollback strategy).

### Acceptance signal (v18 must see)

* `events.jsonl` >= 3 `node_tool_completed` records.
* At least one real filesystem write (e.g. via `write_file` or
  `web_search`).
* Zero `No handler mapped for tool:` strings in any log.
* LLM debug `tools_count > 0` for orgs_v2 nodes.

Tests `test_node_tool_host_executes_real_filesystem_handler`
covers the happy path on disk;
`test_node_tool_host_classifies_plugin_not_loaded` covers the
P0-3 failure-classification observable.

### Out of scope (deferred to Sprint-7+)

* Per-org `<org>/workspace/<node_id>/` filesystem isolation
  (handlers re-use the desktop default_cwd).
* Per-org memory tenancy (handlers re-use the shared
  `memory_manager`).
* Multi-round ReAct -- `MAX_TOOL_ROUNDS` stays at 1
  (Sprint-5 deliberate bound, RCA §1.5.3 out-of-scope #1).
* Permission / approval gate -- we route through plain
  `execute_by_tool`, not the full `ToolExecutor`. Sprint-6
  inherits the same "no gate" stance as Sprint-5 explicitly
  (RCA §1.5.4 risk acknowledged).

## P0-2 -- F2 stop-org telemetry alignment (RCA §2)

### Root cause (recap)

Sprint-5 fixed `cancel_all_for_org` to seed
`_command_outcomes[cid]["cancelled_by"] = "stop_org"` (and the
watchdog mirrored that with `"watchdog"`) but the actual
on-disk events.jsonl writes were emitted by two different
functions that **never consulted the outcome cache**:

* `_runtime_dispatch.cancel_user_command` (line 368-371 pre-fix)
  hard-coded `reason="user_cancel"` on the
  `user_command_cancelled` event.
* `_runtime_agent_pipeline_executor._invoke_agent`'s
  `except CancelledError` (line 252-260 pre-fix) hard-coded
  `reason="user_cancel"` on the `agent_run_cancelled` event.

So v17 audit saw 0/5 R.F2 stop-org cases tagged with
`cancelled_by=stop_org` on disk; the memory marker was a
single-plane fix.

### Decision (RCA §2.5 method A)

Thread the source verbatim through the cancel pipeline and emit
**both** `reason` and `cancelled_by` on the event payload:

* `_runtime_dispatch.cancel_user_command(*, cancel_reason=None)` --
  optional kwarg; defaults to `None` so the Sprint-3
  user-cancel observable (`reason="user_cancel"` + no
  `cancelled_by`) is byte-for-byte compatible.
* `OrgRuntime.cancel_user_command(*, cancel_reason=None)` --
  passthrough.
* `OrgCommandService.cancel_all_for_org(*, reason="stop_org")`
  now passes `cancel_reason=reason` when it calls
  `runtime.cancel_user_command`.
* `OrgCommandService.get_cancel_source(command_id)` -- new
  accessor that returns `_command_outcomes[cid]["cancelled_by"]`
  for the executor's `CancelledError` branch to consult.
* `_runtime_agent_pipeline_executor.__init__(*, cancel_source_provider=None)` --
  optional callback; defaults to `None` so existing tests keep
  the Sprint-5 observable.
* `command_service._watchdog_tick` emits `cancelled_by="watchdog"`
  on the `agent_run_watchdog_killed` event (mirrors the cache).

### Acceptance signal (v18 must see)

For a stop-org case (`POST /api/v2/orgs/<id>/stop`):
* events.jsonl `user_command_cancelled` payload has
  `cancelled_by="stop_org"` AND `reason="stop_org"`.
* events.jsonl `agent_run_cancelled` payload has
  `cancelled_by="stop_org"` AND `reason="stop_org"`.

For a user cancel:
* both payloads still carry `cancelled_by="user_cancel"`.

For a watchdog kill:
* `agent_run_watchdog_killed` payload carries
  `cancelled_by="watchdog"`.

Tests covering this end-to-end:
`test_stop_org_flow_writes_cancelled_by_to_disk` (full flow),
`test_dispatch_cancel_user_command_emits_cancelled_by_stop_org`
(dispatch unit), `test_watchdog_kill_emits_cancelled_by_watchdog_to_disk`,
`test_executor_cancel_consults_cancel_source_provider`.

## P0-3 -- hh_* plugin tool bridge (RCA §4 P0-3)

### Root cause (recap)

Sprint-5 changelog (`_v16_sprint5_p0_changelog.md`) flagged
"`hh_*` plugin tools silently dropped" because Sprint-5
intentionally limited the tool resolver to the static
`tools/definitions/get_tool_definition` table -- plugin
manifests were never consulted. Result: `wb-hh-image`,
`wb-hh-video`, `wb-hh-human`, `wb-hh-long` nodes activated by
D3-ext but came with zero `hh_*` tools, so the LLM had to
hallucinate around the missing plumbing.

### Decision

P0-3 piggybacks on P0-1. The plugin API
(`plugins/api.py:300`) already registers plugin handlers into
`agent.handler_registry` AND extends `agent._tools` with the
definitions. Because `NodeToolHost.lookup_tool_definition`
scans `agent._tools` first, any tool the plugin registers --
including `hh_*` -- becomes discoverable to
`resolve_node_tools` for free.

Failure classification was the only thing Sprint-6 had to
explicitly add: if a node spec lists an `hh_*` tool the plugin
has not loaded, the host raises `ToolNotAvailable`, and
`execute_node_tool` translates that into
`node_tool_failed` with `reason="plugin_not_loaded"` (not the
generic `"handler_raised"` string the Sprint-5 path would have
produced).

### Acceptance signal (v18 must see)

* `wb-hh-image` node LLM debug `tools_count` includes
  `hh_image_*` entries.
* "Generate a 30-second Valentine teaser cover image using
  wb-hh-image" produces a real artefact (not pure text).
* If the plugin is not loaded, events.jsonl shows
  `node_tool_failed reason=plugin_not_loaded` instead of the
  generic `node_tool_failed reason=handler_raised`.

Tests: `test_resolve_node_tools_picks_up_plugin_definitions`
(plugin-tool discovery via host) +
`test_node_tool_host_classifies_plugin_not_loaded` (disk
classification).

## Pattern sweep results

### Pattern 1 -- in-memory cache vs on-disk events parity

We grep'd `src/openakita/orgs/` for every `_command_outcomes`
write/read and cross-referenced each `events.jsonl`-bound emit.
The four cancellation paths (`user_command_cancelled`,
`agent_run_cancelled`, `agent_run_watchdog_killed`, plus the
already-correct `agent_run_failed` reason/error mirror) are now
the only events that need the parity invariant, and all four
write the same `cancelled_by` / `reason` to both planes after
Sprint-6.

`agent_run_started` / `agent_run_finished` carry no parity
field (the cache marks them with `event_ref` only, no extra
field). `node_tool_called` / `node_tool_completed` /
`node_tool_failed` write to events.jsonl only -- no cache
parity concern.

### Pattern 2 -- mock-only tests missing disk observable

Sprint-5's `test_node_tool_injection.py` correctly asserted
that `default_handler_registry.execute_by_tool` was called but
did not write a real events.jsonl, which is exactly why the
unpopulated-global root cause slipped past CI. Sprint-6 adds
real-disk integration tests:

* `test_node_tool_host_integration.py` -- 5 cases (RCA §3
  reverse: "if the code path doesn't actually write to disk in
  the test, the test cannot tell on-disk lying about it
  apart from the truth").
* `test_cancelled_by_disk_integration.py` -- 7 cases including
  end-to-end stop-org / watchdog / user-cancel flows that
  open `events.jsonl` and read the payloads.

Both new test modules use a real `DiskWiredEventBus` /
`_PersistentEventBus` fixture so the assertions are on the
actual JSONL file content rather than `MagicMock.assert_called_with`.

### Pattern 3 -- placeholders / Optional defaults / duck-call / trace_context

None new introduced. `NodeToolHost.execute_tool` tags the
trace context via `agent.brain.set_trace_context` when
available (best-effort), matching Sprint-4's
`set_trace_context` convention. All new kwargs have explicit
`None` defaults so existing callers (tests, gateway, IM
bridge) are unchanged.

## Review rounds

### Round 1 -- code quality

* `NodeToolHost` re-uses the main desktop Agent's handler
  registry; no duplicate state. The `dispose()` path on rebind
  prevents reference leakage on hot reload.
* `cancel_reason` propagation has matching type signatures
  (`str | None` default `None`) through three layers
  (`OrgRuntime`, dispatch, command_service); all callers either
  pass the source verbatim or omit the kwarg (preserves
  Sprint-3 observable).
* `asyncio.CancelledError` continues to propagate unchanged
  from `NodeToolHost.execute_tool` -> `execute_node_tool` ->
  pipeline executor; the Sprint-3 P0-2 cancel invariant is
  intact (verified by `test_cancel_propagates`).
* Sprint-2 / 3 / 4 / 5 P0 tests all still pass (`H1-H4`,
  outcomes index, DispatchCallback, DefaultAgentBuilder).
* Edge cases checked: unknown tool (-> `ToolNotAvailable`),
  handler raise (-> generic `handler_raised`), plugin not
  loaded (-> `plugin_not_loaded`), spec without
  `external_tools` (host falls back to legacy whitelist),
  cancel during tool execution (CancelledError re-raised).
* No placeholders or `pass # not implemented` markers added.

### Round 2 -- CI / lint / mypy / tests

* `ruff check src/openakita/orgs/_runtime_agent_host.py
  src/openakita/orgs/_runtime_node_tools.py
  src/openakita/orgs/_default_agent_builder.py
  src/openakita/orgs/_runtime_dispatch.py
  src/openakita/orgs/_runtime_agent_pipeline_executor.py
  src/openakita/orgs/command_service.py
  src/openakita/orgs/runtime.py
  src/openakita/api/server.py
  tests/runtime/orgs/test_node_tool_host_integration.py
  tests/runtime/orgs/test_cancelled_by_disk_integration.py`
  -- **All checks passed**. The 6 pre-existing ruff errors
  (`I001` in `core/_brain_legacy.py`, `manager.py`; `F822` in
  `core/errors.py`) are baseline on `HEAD=5960bf3e` and not
  touched in this sprint.
* `mypy src/openakita/orgs/_runtime_agent_host.py
  src/openakita/orgs/_runtime_node_tools.py
  src/openakita/orgs/_default_agent_builder.py
  src/openakita/orgs/_runtime_dispatch.py
  src/openakita/orgs/_runtime_agent_pipeline_executor.py
  src/openakita/orgs/command_service.py
  src/openakita/orgs/runtime.py`
  -- **Success: no issues found in 7 source files**.
* `pytest tests/runtime/ tests/api/test_server_app_wiring.py
  tests/api/test_openapi_plugin_immunity.py
  -k "not test_b19_create_node_schedule"` -- **943 passed**.
* Two pre-existing baseline failures verified on
  `HEAD=5960bf3e` independent of Sprint-6:
    * `tests/parity/orgs/test_frontend_stale_paths_sentinel.py::test_frontend_no_unauthorized_orgs_spec_paths` -- documentation
      comment in `apps/setup-center/src/api/orgs.ts:2` that
      already mentions `/api/v2/orgs-spec/...` in a comment;
      not Sprint-6 territory.
    * `tests/api/test_p97_beta_smoke.py::test_b19_create_node_schedule` -- baseline 422 on a schedule
      endpoint; not Sprint-6 territory.
* `python -m openakita --help` -- CLI loads cleanly with the
  new wiring. No new dependencies (`git diff --stat
  pyproject.toml` is empty).
* All three new integration tests **real-disk read**
  events.jsonl (Pattern 2 requirement):
  `test_node_tool_host_executes_real_filesystem_handler`,
  `test_node_tool_host_classifies_plugin_not_loaded`,
  `test_stop_org_flow_writes_cancelled_by_to_disk` +
  4 more sibling cases.

## Next steps for the user

1. Restart the backend (`openakita serve`).
2. Run the v18 exploratory test pass, checking the audit
   signals:
   * R.D4 -- `events.jsonl` should now have
     >= 3 `node_tool_completed` records and zero
     `"No handler mapped for tool:"` strings.
   * R.F2 -- send a long-running command, then `POST
     /api/v2/orgs/<id>/stop`. `events.jsonl` should carry
     `cancelled_by="stop_org"` on both the
     `user_command_cancelled` and `agent_run_cancelled`
     events.
   * R.WB-HH (hh_*) -- activate `wb-hh-image` /
     `wb-hh-video` / `wb-hh-human` / `wb-hh-long` and check
     LLM debug `tools_count`. If the plugin manifest is not
     loaded the failure mode is now
     `node_tool_failed reason=plugin_not_loaded` instead of
     the v17 silent drop.

## Open items (out of scope for Sprint-6)

* **Multi-round ReAct** -- `MAX_TOOL_ROUNDS` stays at 1.
* **MCP server injection** for orgs_v2 nodes -- the host
  re-uses the desktop MCP wiring, no per-org bring-up.
* **Inter-node memory retrieval at prompt time** -- the
  prompt builder still reads only the per-node memory scope.
* **Parallel child dispatch** -- the orchestrator stays
  serial.
* **Per-org filesystem / memory tenancy** -- handlers re-use
  the desktop defaults; isolation is a Sprint-7 task.
* **policy_v2 approval gate for orgs_v2 nodes** -- we route
  through `execute_by_tool` directly; the full `ToolExecutor`
  gate is a Sprint-7+ item.
