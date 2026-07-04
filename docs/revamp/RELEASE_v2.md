# OpenAkita v2.0.0-rc2 -- Release notes

**Tag:** ``v2.0.0-rc2`` (local-only; not pushed)
**Branch:** ``revamp/v2``
**Cut at:** end of P-RC-8 (G-RC-8 signed 2026-05-19)

## What v2.0.0-rc2 ships beyond v2.0.0-rc1

The post-RC **continuation plan**
(`openakita_revamp_continuation_plan_d6192647.plan.md`) ran from
P-RC-0 through P-RC-8 -- **all eight phases complete** -- and
landed roughly **85 commits** on ``revamp/v2``. This is the
endgame release of that plan.

* **All five facade sentinels closed.** ``agent/brain.py``,
  ``agent/tools.py``, ``agent/context.py``, ``agent/reasoning.py``,
  ``agent/core.py`` no longer carry the
  ``REVAMP-FACADE-ALLOWED-UNTIL`` sentinel comment; each is a real
  v2 implementation composing extracted helpers from
  ``runtime/llm``, ``runtime/io``, ``runtime/context``,
  ``runtime/state_graph``, ``runtime/desktop``, and
  ``agent/safety``.

* **Five giants slimmed (shims deleted entirely by P-RC-7).** The
  legacy lazy shims ``core/agent.py``, ``core/brain.py``,
  ``core/context_manager.py``, ``core/reasoning_engine.py``, and
  ``core/tool_executor.py`` were deleted at commit ``a21cdd4b``
  after every production caller (~107 imports across 64 files)
  and every test (~132 imports across 69 files) was migrated to
  ``openakita.agent.*`` / ``openakita.runtime.*``. The
  ``_*_legacy.py`` private files stay -- the v2 classes inherit
  from them byte-faithfully -- and ``core/supervisor.py`` was
  renamed to ``core/_supervisor_legacy.py`` at P-RC-8 P8.0
  (``caf5d7f3``) to mirror the same legacy-private convention.

* **Runtime packages all shipped.** ``runtime/llm`` (4 helpers),
  ``runtime/io`` (2 helpers + retry policy), ``runtime/context``
  (3 helpers), ``runtime/desktop`` (attachments), ``runtime/state_graph``
  (full Pregel router with conditional + delegation routing),
  ``runtime/orgs`` (JSON + SQLite stores + 18-case cross-backend
  contract suite), plus ``agent/safety`` (destructive-intent
  classifier).

* **All 10 ADRs Accepted.** ADR-0001..ADR-0010 flipped from
  ``Status: Proposed`` to ``Status: Accepted`` at P-RC-8 P8.2
  (``b1fb4cd7``). Each ADR has an ``Accepted: 2026-05-19`` line
  pointing at the G-RC-8 sign-off after eight phases of shipped
  implementation against the spec.

* **Acceptance criteria documented.**
  ``docs/revamp/ACCEPTANCE.md`` (P-RC-8 P8.3 ``709767b3``)
  records 4-Pass + 1-Partial against the original plan section 9
  criteria. The 1-Partial is criterion 5 ("one-click create from
  any template UI default") which depends on the
  ``src/openakita/orgs/`` deletion that ``docs/revamp/P-RC-9-CHARTER.md``
  (P-RC-8 P8.4 ``483b8b13``) defers to a future P-RC-9 plan.

## Test snapshot (P-RC-8 final, end of P8.6)

* **Main gate** (``tests/runtime tests/agent tests/api
  tests/parity tests/unit/test_plugins``): **1123 passed / 1
  skipped / 5 xfailed** (vs P-RC-7's 1122 / 1 / 5 -- one new
  smoke test ``test_brain_get_current_endpoint_info_smoke``
  landed in P8.0).
* **Canary integration**
  (``tests/integration/test_v2_im_canary_e2e.py`` +
  ``tests/integration/test_v2_im_cancel.py``):
  **5/5 passed.**
* **Storage contract suite**
  (``tests/runtime/orgs/test_store_contract.py``):
  **18/18 passed** (9 cases x JSON + SQLite backends).
* **Ruff over the v2 surface** (``src/openakita/runtime``,
  ``src/openakita/agent``, ``src/openakita/plugins/manager.py``,
  ``tests/runtime``, ``tests/agent``, ``tests/api``,
  ``tests/parity``): clean.
* **LOC audit** (``python scripts/revamp_loc_audit.py``):
  exit 0; every tracked file within cap (5 legacy renames + 1
  supervisor rename visible as informational-only at their
  current sizes; the four agent giants at 336..369 LOC vs the
  pre-P-RC-4 ~2000 LOC each).

## Deferred to P-RC-9

The wholesale ``src/openakita/orgs/`` integral migration is **not**
executed in the continuation plan. See
``docs/revamp/P-RC-9-CHARTER.md`` for the deferred-work charter:

* ~880 KB / 26 files / 86 production import sites unchanged.
* Six v2 subsystems must be written first: OrgManager,
  OrgRuntime, OrgCommandService, OrgBlackboard, ProjectStore,
  NodeScheduler.
* Estimated 4-6 weeks, ~30-50 commits, separate parity harness.
* Awaits its own G-RC-9 gate review.

Operators running v2 in production today should keep the legacy
``src/openakita/orgs/`` surface live. The v2 ``runtime/`` +
``agent/`` surfaces co-exist with ``orgs/`` cleanly (only the
five lazy ``core/*.py`` shims were deleted in P-RC-7).

## Tag

```
git tag -a v2.0.0-rc2 -m "OpenAkita v2.0.0-rc2: continuation plan complete (P-RC-0..8); orgs/ migration deferred to P-RC-9"
```

Local only; not pushed (mirrors the rc1 tagging policy).

---

## v2.0.0-rc1 -- Release notes (historical)

**Tag:** ``v2.0.0-rc1`` (local-only; not pushed)
**Branch:** ``revamp/v2``

## What v2 ships

Phase 0 → Phase 7 of the backend revamp plan are complete. The
``src/openakita/runtime/`` and ``src/openakita/agent/`` packages
contain the new orchestration stack; the ``src/openakita/core/`` and
``src/openakita/orgs/`` legacy packages remain installed so existing
callers (gateway, channels, api/routes/orgs.py, ~50 test files)
continue to operate. ``settings.runtime_v2_enabled`` defaults to
``True``: HTTP v2 endpoints are served, the channels canary log
emits, and the migration script populates ``data/orgs_v2.json``.

### Runtime (Phase 1, 3, 4, 5)

* ``runtime/models.py`` — ``OrgV2``, ``NodeV2``, ``EdgeV2``,
  ``NodeType``, ``EdgeKind`` (ADR-0002).
* ``runtime/cancel_token.py``, ``retry_policy.py``, ``stream.py``,
  ``event_store.py``, ``checkpoint.py``, ``backends/sqlite.py``,
  ``backends/json_file.py``.
* ``runtime/ledger.py``, ``runtime/stall_detector.py``,
  ``runtime/supervisor.py``, ``runtime/messenger.py``,
  ``runtime/guardrail/``, ``runtime/state_graph.py``.
* ``runtime/nodes/`` — ``BaseNode`` + 5 node types + manifest.
* ``runtime/templates/`` — schema + registry + 4 built-in templates
  (``aigc_video_studio``, ``software_team``, ``startup_company``,
  ``content_ops``).

### Agent (Phase 2)

* All MOVE commits 6 → 12: ``agent/permission``, ``agent/audit``,
  ``agent/validators``, ``agent/pending_approvals``,
  ``agent/confirmation``, ``agent/ui_confirm_bus``, ``agent/hooks``,
  ``agent/token_budget``, ``agent/resource_budget``,
  ``agent/loop_budget``, ``agent/tool_result_budget``,
  ``agent/skill_manager``, ``agent/capabilities``,
  ``agent/security_actions``, ``agent/file_history``,
  ``agent/trusted_paths``, ``agent/domain_allowlist``,
  ``agent/lsp_feedback``, ``agent/sandbox``, ``agent/docker_backend``,
  ``agent/desktop_notify``, ``agent/sse_replay``, ``agent/ralph``,
  ``agent/trait_miner``, ``agent/user_profile``.
* All REWRITE commits 14 → 18: ``agent/tools``, ``agent/context``,
  ``agent/brain`` (with the new ``SupervisorBrain`` Protocol),
  ``agent/reasoning``, ``agent/core``. These ship as
  re-export facades that fix the new canonical import surface and
  document what is deferred (deep state-graph integration, streaming
  extraction, etc.) to the post-RC mechanical cleanup.

### Parity (commits 13, 19)

* ``tests/parity/`` — 30 baseline cases across 14 kinds. 30/30
  passing (100%), well above the G2 ≥ 95% threshold.

### API + channels + frontend (Phase 6)

* ``/api/v2/orgs/templates`` (list / get / instantiate) and
  ``/api/v2/orgs`` (full CRUD) — gated by
  ``settings.runtime_v2_enabled``.
* ``runtime/orgs/store.py`` JSON-file-backed v2 org store.
* ``runtime/channel_routing.py`` ``RoutingPlan`` helper +
  ``channels/gateway.py`` canary log hook (``[IM v2-canary]``).
* ``apps/setup-center/src/api/orgs.ts`` v2 client wrappers.
* ``apps/setup-center/src/components/TemplatePickerDrawer.tsx``
  shadcn-Sheet picker.

### Cutover (Phase 7)

* ``scripts/migrate_orgs_to_v2.py`` re-entrant migration script.
* ``settings.runtime_v2_enabled`` default ``True``.
* ``docs/revamp/burn_in.md`` 6-section runbook with smoke checklist,
  observability log prefixes, daily ritual, exit criteria, roll-back
  drill, and promotion path.

### Gate review notes

``docs/revamp/gates/G1.md`` → ``G7.md`` all written. G2 records
30 / 30 parity pass.

## What is staged for post-RC mechanical cleanup

Phase 8 of the original plan called for wholesale removal of
``src/openakita/orgs/`` and the shimmed ``src/openakita/core/*``
modules. A workspace-wide importer audit during this release cycle
revealed that the cut would require pre-migrating ~20 production
files (``channels/gateway.py``, ``api/server.py``,
``api/routes/orgs.py``, ``api/routes/chat.py``,
``agents/factory.py``, ``orgs/runtime.py``, ``orgs/command_service.py``,
``orgs/tool_handler.py``, ``orgs/project_store.py``, …) and ~50
test files. That work is honest engineering effort that does not
fit inside the burn-in window — it gates on the burn-in confirming
v2 is actually picking up production traffic, at which point the
legacy importers can be retired one file at a time.

The single safe deletion that *did* land in this RC is
``src/openakita/core/state.py`` — the audit-confirmed zero-importer
``StateStore`` / ``AppState`` module (see ``docs/revamp/core_audit.md``
"DELETE at Phase 8" table). Every other ``core/`` file still has
live callers in legacy code or is a shim that v2 facades re-export
from.

### Post-RC mechanical cleanup checklist (gated by burn-in)

1. ``src/openakita/orgs/`` removal — needs ``channels/gateway.py``
   and ``api/routes/orgs.py`` rewritten to use ``runtime/orgs/`` and
   ``runtime/state_graph``.
2. ``src/openakita/core/`` shim removal — needs callers in
   ``sessions/``, ``memory/``, ``tools/``, ``agents/`` to switch from
   ``openakita.core.*`` to ``openakita.agent.*``.
3. ``core/agent.py``, ``core/brain.py``, ``core/reasoning_engine.py``,
   ``core/tool_executor.py``, ``core/context_manager.py`` deep slim
   refactor — the REWRITE commits 14 → 18 documented these as
   "facade now; deep refactor at Phase 8". Each will be its own
   multi-commit chain after the importers are migrated.
4. Optional rename ``core/task_monitor.py`` → ``runtime/standup.py``
   (plan §8). Defer until callers are reduced.
5. G8 gate review note.

## Test snapshot

* ``tests/runtime``: full pass.
* ``tests/agent``: full pass.
* ``tests/api``: full pass (25/25 on the orgs_v2 surface alone).
* ``tests/unit/test_plugins``: full pass.
* ``tests/parity``: 30/30 cases pass (100%).
* Combined: **755 passed, 1 skipped**.
* ``ruff check`` over the v2 surface: clean.
* Frontend ``npm run build``: succeeds.

## How to roll back

Set ``RUNTIME_V2_ENABLED=false`` in ``.env`` and restart. Legacy
endpoints take over; the v2 facade endpoints return 404. The
legacy ``data/orgs.legacy.db`` backup is preserved by the
migration script if you need to restore it to ``data/orgs.db``.

## Tag

``git tag v2.0.0-rc1`` — local only, not pushed (per plan).
