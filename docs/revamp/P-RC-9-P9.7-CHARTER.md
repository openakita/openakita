# P-RC-9 P9.7 Charter -- v2 REST endpoint mint (planning round)

**Status: PLANNED, NOT EXECUTED.** Planning charter for P9.7,
the seventh phase of P-RC-9 (per ``docs/revamp/P-RC-9-PLAN.md``
section 4 P9.7). P9.7 mints the v2 REST surface that wires the
FastAPI HTTP layer to the six P9.1-P9.6 subsystems
(OrgBlackboard / ProjectStore / NodeScheduler /
OrgCommandService / OrgManager / OrgRuntime) shipped in
P-RC-9 sub-phases 1 through 6.

**Branch**: ``revamp/v3-orgs``.
**HEAD at authorship**: ``89703a28`` (G-RC-9.6 + P9.6.nit2
post-flight closed; 6/6 ADR-0011 subsystems active; 6/6
parity sentinels green; 13 pre-existing main-gate failures
untouched).
**Scope**: planning artifacts only. ``git diff
89703a28..HEAD -- src/openakita/ tests/`` is empty bytes
both before and after this commit.

## 1. Endpoint inventory + classification

Surface measured 2026-05-20 on HEAD ``89703a28`` via
``Select-String -Path src/openakita/api/routes/orgs*.py
-Pattern '^\s*@router\.(get|post|put|delete|patch|websocket)'``.

| file | LOC | endpoints | source phase |
|---|--:|--:|---|
| ``api/routes/orgs.py`` (v1) | 2 533 | **86** | pre-P-RC-9 |
| ``api/routes/orgs_v2.py`` | 337 | **8** | P-RC-3 |
| ``api/routes/orgs_v2_stream.py`` | 139 | **1** SSE | P-RC-3 |
| **Total** | 3 009 | **95** | -- |

**Ledger header "80 endpoints" claim is approximate.** Real
v1 count is **86**; v2 mint target after Group C
deprecations is **~80**, matching the ledger figure
directionally. G-RC-9.7 restates as "86 v1 + 9 v2
already-present; ~80 v2 mint after Group C pruning".

### 1.1 Group A -- already-v2 (9; P-RC-3 vintage)

8 CRUD + 1 SSE under ``/api/v2/orgs/``. Wires to
``runtime.orgs.JsonOrgStore`` over the ``OrgV2`` template
data model -- **disjoint** from the P9.5 ``OrgManager``
``Organization`` model the bulk v1 surface uses.

Paths: ``GET /templates``, ``GET /templates/{id}``,
``POST /templates/{id}/instantiate``, ``GET|POST /``,
``GET|PATCH|DELETE /{org_id}``, ``GET /{org_id}/stream`` (SSE).

**Data-model collision risk**: ``GET /api/v2/orgs`` is
claimed by Group A (returns ``{orgs: [OrgV2.to_jsonable()]}``)
AND by the P9.7 Group B mint (would return v1-shape
``mgr.list_orgs(...)``). Response shapes are **not
schema-compatible**. P9.7alpha-1 must decide:

* **R1**: rename Group A to ``/api/v2/orgs-spec/...``.
* **R2**: multiplex via ``?model=`` query param. Rejected
  -- breaks OpenAPI codegen.
* **R3** (RECOMMENDED): R1 + 308 Permanent Redirect shims
  at the old paths through v2.0.x. Cost ~30 LOC + frontend
  rewiring across ~5 call sites.

First operator-facing question P9.7 must resolve.

### 1.2 Group B -- v1 endpoints needing v2 replacement (~80)

86 v1 minus ~6 Group C = **~80**. By functional cluster
(matches PLAN section 1d 12-group breakdown):

| cluster | count | v2 subsystem wiring |
|---|--:|---|
| Org CRUD + lifecycle + templates | 17 | OrgManager (list/create/get/update/delete/duplicate/archive/unarchive/save-as-template/export/import/from-template/templates/avatars) |
| Node lifecycle + identity + mcp + schedules | 16 | OrgManager + NodeScheduler + OrgRuntime NodeMessageRouter |
| Runtime control + Commands + Broadcast | 9 | OrgRuntime OrgLifecycleManager + OrgCommandService + CommandDispatchManager |
| Node status + observability + Stats | 6 | OrgRuntime + OrgManager status snapshot |
| Memory + Events + Activity + Messages + audit + Policies | 17 | OrgBlackboard + OrgRuntime event_store + OrgCommandService + OrgManager policies CRUD |
| Inbox + Scaling + Reports + retained-debug | 16 | OrgRuntime inbox + scaling/recruit/clone + reports + (subset C-pruned) |
| Projects + tasks | 11 | ProjectStore (CRUD + tree + timeline + dispatch + cancel) |

Total = 86; Group C section 1.3 removes ~6.

### 1.3 Group C -- v1 endpoints retired (~6)

Stay live in v1 ``orgs.py`` until P9.9 -- become 410 Gone
per Q-B (P-RC-9) ACCEPTED (b) 1-release shim
(``Q_DECISIONS.md``).

| method | path | rationale |
|---|---|---|
| POST | ``/{id}/reset`` | replaced by stop + delete + create-from-template |
| POST | ``/{id}/heartbeat/trigger``, ``/{id}/standup/trigger``, ``.../schedules/{sid}/trigger`` | debug-only; v2 fires autonomously / ops-only |
| GET | ``/{id}/events/replay`` | replaced by SSE replay-from-checkpoint |
| POST | ``/{id}/im-reply`` | routes via ``channels/gateway.py`` directly |

Operator can reclassify any Group C row to Group B at
P9.7alpha-1 (non-destructive because v1 stays live).

## 2. LOC budget

PLAN section 4 P9.7 = **~1 800 src LOC**. Re-estimate:

| component | LOC | basis |
|---|--:|---|
| ~80 endpoint bodies | 1 440 | avg 18 LOC (3 sig + 5-10 body + 3-5 HTTPException) |
| Pydantic request/response models | 270 | ~22 shapes x 12 LOC + enums |
| Shared FastAPI dependencies | 120 | 5-8 ``Depends(...)`` factories + error helpers |
| Module preambles + router setup | 80 | 4-6 sub-modules x ~15 LOC |
| **Total v2 src LOC** | **~1 910** | -- |

**Verdict**: ~6 % over PLAN; inside ADR-0014's standard
~10 % planning tolerance. **No ADR-0015 needed.** Escape
hatch (P9.6 turn-1 precedent): if P9.7alpha turn-1 finds
per-endpoint avg > 25 LOC, STOP and write ADR-0015 using
ADR-0014 format. Precedent in place; not pre-authorised.

Test LOC budget: contract ~1 600 + integration ~250 + SLA
~150 + sentinel ~80 = **~2 080**, double PLAN's ~900
estimate (PLAN under-counted per-endpoint density).
ADR-0014 gates SRC LOC only; no test-budget ADR needed.

## 3. Phase breakdown (alpha / beta / gamma)

### P9.7alpha -- scaffold + Group A reconciliation (2-3 commits)

* **alpha-1** (docs only, ~150 LOC): resolve section 1.1
  Group A reconciliation (recommend R3), record frontend
  port flip recon (PLAN expected
  ``apps/setup-center/src/config.ts`` but file does not
  exist at ``89703a28``; alpha-1 records actual config
  location), STATUS.md pointer.
* **alpha-2** (~270 LOC): ``orgs_v2_models.py`` NEW -- ~22
  Pydantic shapes + shared enums.
* **alpha-3** (~120 LOC, optional): ``_orgs_v2_deps.py``
  NEW -- shared ``Depends(...)`` + error helpers.
Total alpha: **~540 LOC / 2-3 commits**.

### P9.7beta -- endpoint groups (6-7 commits)

One commit per cluster, sized to ~250-340 LOC under <= 380
WARN:

| commit | cluster | endpoints | LOC |
|---|---|--:|--:|
| beta-a | Org CRUD + Templates | 17 | ~280 |
| beta-b | Node lifecycle + schedules | 16 | ~270 |
| beta-c | Runtime control + Commands + Broadcast | 9 | ~280 |
| beta-d | Node status + Memory + Events + Stats | 12 | ~290 |
| beta-e | Messages + Policies + Inbox | 16 | ~280 |
| beta-f | Scaling + Reports + retained-debug | 10 | ~250 |
| beta-g | Projects + Tasks | 11 | ~220 |

7 commits, **~1 870 LOC**. Each appends to growing
``orgs_v2.py``, or splits into ``orgs_v2_runtime.py`` /
``orgs_v2_manager.py`` / ``orgs_v2_projects.py`` if running
LOC exceeds ~1 200 (split decision at beta-a body).
### P9.7gamma -- contract tests + sentinel + gate (3-4 commits)

* **gamma-1** (~1 600 LOC, possibly split a/b): contract
  tests for every v2 endpoint (happy + error paths).
* **gamma-2** (~400 LOC): integration flows + SLA
  perf_counter cases.
* **gamma-3** (~80 LOC): REST contract sentinel (section 7).
* **gamma-4** (~350 LOC): ``gates/G-RC-9.7.md`` mini-gate +
  ledger close + ACCEPTANCE.md #5 upgrade Partial -> Pass.
Total gamma: **~2 430 LOC / 3-4 commits**.

### Phase totals

**11-14 commits / ~2 490 src LOC / ~2 000 test LOC / ~480
doc LOC**, vs PLAN's 8-12 commits + 1 800 src + 900 test:
+2-3 commits (Group A reconciliation + Pydantic split +
sentinel), +6 % src LOC (inside ADR-0014 tolerance), ~2x
test LOC.

## 4. Subsystem wiring matrix (representative)

Wiring is **thin** -- every endpoint delegates to 1-2
P9.1-P9.6 subsystem methods. Full table grows during
P9.7alpha recon; representative rows:

| endpoint | wiring |
|---|---|
| ``POST /command`` | ``svc.submit(OrgCommandRequest(...))`` |
| ``POST /commands/{cid}/cancel`` | ``svc.cancel(...)`` -> ``runtime.cancel_user_command(...)`` |
| ``POST /start`` | ``runtime.lifecycle.start_org(...)`` |
| ``GET /nodes/{nid}/status`` | ``runtime.get_node_status(...)`` |
| ``GET /projects`` and ``POST /projects/{pid}/tasks/{tid}/dispatch`` | ``project_store.list_projects()``; ``project_store.get_task()`` + ``svc.submit(...)`` |
| ``GET /memory``, ``GET /inbox`` | ``blackboard.read(...)``; ``runtime.get_inbox(...).list()`` |
| ``GET /nodes/{nid}/schedules`` | ``mgr.get_node_schedules(...)`` |

Thin wiring keeps per-endpoint LOC near 18 lines (vs v1
avg ~30 LOC because v1 contained business logic now hoisted
into P9.1-P9.6 subsystems).

## 5. Parity strategy -- explicit DEPARTURE from P9.1-P9.6

P9.1-P9.6 each shipped ``test_<subsystem>_parity.py``
asserting v1 vs v2 equal output. P9.7 **does not** follow:

* **Coupling**: v1 REST routes import v1 OrgManager /
  OrgRuntime / OrgCommandService via ``request.app.state``.
  Importing both v1 and v2 in the same pytest process
  triggers the circular-import / app-state-collision problems
  ADR-0011 was designed around.
* **Test framework**: FastAPI ``TestClient`` binds to one
  ``app``; two parallel apps per fixture is integration,
  not parity in the P-RC-9 sense.

**Decision**: ship **REST contract tests** via ``TestClient``
against a fresh test app with only the v2 router mounted
(the P-RC-3 ``tests/api/test_orgs_v2.py`` is the template).
Assertions: (1) status code matches contract; (2) response
body validates against Pydantic response model or matches
frozen JSON snapshot under
``tests/api/snapshots/orgs_v2/<endpoint>.json``; (3)
side-effects on subsystem state.

**Golden-file capture is OPTIONAL**, applied only to the
~12 endpoints the frontend reads shape-for-shape (Org CRUD
+ Node status + Memory read + Stats). For these, P9.7alpha
captures v1 responses to ``tests/api/snapshots/orgs_v1/``
and gamma asserts equivalence. Others get contract tests
only because their wire schema is v2-redesigned.

## 6. Test count estimate

| family | cases | LOC | location |
|---|--:|--:|---|
| Contract (TestClient happy + error) | ~120 | ~1 600 | ``tests/api/test_orgs_v2_contract*.py`` |
| Integration (cross-endpoint flows) | ~10 | ~250 | ``tests/api/test_orgs_v2_integration.py`` |
| Wall-clock SLA (ADR-0013) | 3-5 | ~150 | ``tests/runtime/test_orgs_v2_sla.py`` |
| Snapshot equivalence (12 critical) | ~12 | absorbed | inline in contract |
| REST contract sentinel | 1 | ~80 | ``tests/api/test_orgs_v2_sentinel.py`` |
| **Total NEW cases** | **~140-150** | **~2 080** | -- |
Narrowed-slice baseline (G-RC-9.6) 1 457p / 12s / 5xf;
projected after G-RC-9.7: **1 597-1 607p / 12s / 5xf** (+140
to +150).

## 7. Sentinel strategy

P9.6 closed the last of the 6 P-RC-9 parity sentinels (all
6 ADR-0011 subsystems active, 0 ``@pytest.mark.xfail``).
P9.7 introduces no NEW parity sentinel (per section 5).

P9.7gamma-3 ships a **REST contract sentinel**:

```python
# tests/api/test_orgs_v2_sentinel.py
def test_every_v2_orgs_endpoint_has_a_contract_test() -> None:
    """Iterate orgs_v2.router.routes; assert each (path, method)
    pair is referenced by at least one contract test ID."""
    from openakita.api.routes import orgs_v2
    expected = {(r.path, m) for r in orgs_v2.router.routes
                for m in (r.methods or set())}
    referenced = _scan_contract_test_files()
    missing = expected - referenced
    assert not missing, f"endpoints without contract test: {missing}"
```

Collection-based (no test execution needed); enforces full
REST coverage and catches the future regression where a v2
endpoint lands without a contract test. Active sentinels
after G-RC-9.7: **6 parity + 1 REST contract = 7**.

## 8. Risks + mitigation

* **R1 -- Group A path collision (HIGH, certain)**. Existing
  ``/api/v2/orgs`` paths collide with the mint over a
  different data model. **Mitigation**: alpha-1 picks R3
  (move to ``/api/v2/orgs-spec/...`` + 308 shims for
  v2.0.x); ~30 LOC + frontend rewiring across ~5 sites;
  frontend team notified at alpha-1.
* **R2 -- frontend port flip recon gap (MEDIUM, possible)**.
  PLAN expected ``apps/setup-center/src/config.ts`` for the
  default-port flip; the file does not exist at ``89703a28``.
  **Mitigation**: alpha-1 records actual frontend config
  location; build-artifact test asserts ``dist-web``
  ``BUILD_INFO.api_default == '/api/v2'`` regardless.
* **R3 + R5 -- contract-test overshoot + v1 drift (MEDIUM)**.
  ~2 000 test LOC is 2x PLAN; v1 has undocumented quirks
  (``POST /command`` accepts both ``content`` / ``message``).
  **Mitigation**: ``@pytest.mark.parametrize`` for repeated
  patterns (not-found / unauthorised / org-not-running)
  compresses boilerplate; split gamma-1 into a/b if a
  single commit breaches <= 380 WARN. Snapshot capture for
  12 frontend-critical endpoints catches likely drift;
  wire-level v1 parity is NOT a deliverable for the other
  68.
* **R4 -- ADR-0011 Protocol granularity pressure (LOW)**.
  Resist introducing ``RestAuthProtocol`` etc.; FastAPI
  ``Depends`` is the boundary, not a new Protocol. Keep
  DI flat at 6 factories.

## 9. Gate criteria for G-RC-9.7

1. All ~80 v2 endpoints have >= 1 contract test (sentinel
   passes).
2. ~80 v2 endpoints registered under ``/api/v2/orgs/...``.
3. Group A endpoints moved per R3 (or alternative approved
   at alpha-1); 308 redirect shims live for v2.0.x.
4. v1 endpoint file NOT touched: ``git diff 89703a28..HEAD
   -- src/openakita/api/routes/orgs.py`` empty bytes.
5. Boundary discipline: changes ONLY under
   ``src/openakita/api/routes/orgs_v2*.py`` and (any tiny
   tweaks) ``src/openakita/runtime/orgs/``;
   ``src/openakita/orgs/`` untouched.
6. Main gate stays green: full ``pytest -q --tb=no`` passes
   >= G-RC-9.6 baseline (6 538 passed); 13 pre-existing
   failures unchanged.
7. ADR-0014 discipline: src LOC <= ~1 910 (target) or
   <= ~2 100 (10 % tolerance); ADR-0015 only if exceeded.
8. ACCEPTANCE.md #5 upgraded Partial -> Pass in the same
   gate commit per PLAN section 8.
9. ``docs/revamp/gates/G-RC-9.7.md`` mini-gate lands in
   gamma-4; mirrors G-RC-9.6 structure.

## 10. P9.8 / P9.9 preview (what P9.7 does NOT do)

* **P9.8 -- caller migration** (~86 src + 216 test import
  sites). ``from openakita.orgs.X`` mechanically rewritten
  to ``runtime.orgs.X``. v1 ``api/routes/orgs.py`` keeps
  serving v1 until P9.9; frontend default-port flip lands
  here if R2 recon needs source change.
* **P9.9 -- physical deletion + 410 shim** (ADR-0012 + Q-B).
  ``git rm -r src/openakita/orgs/``; v1 ``orgs.py`` becomes
  a 410-Gone shim ``{gone: true, moved_to: "/api/v2/..."}``;
  ``tests/orgs/`` deleted as a block.
* **P9.10** ships G-RC-9 final + ACCEPTANCE.md upgrades +
  ``v2.0.0-rc3`` tag (P9.7 is the LAST P-RC-9 phase
  introducing NEW v2 surface).

## 11. Reference matrix (NIT-E-1 discipline)

Re-scan of ``d:\claw-research\repos`` (autogen / cortex /
crewAI / langgraph / MetaGPT / sint-protocol) +
``briefs`` (01-cortex / 02-sint / 03-langgraph / 04-metagpt
/ 05-crewai / 06-autogen) for REST endpoint / FastAPI /
contract-testing patterns. Per NIT-E-1 each input is
marked **rejected** explicitly:

All 12 items **rejected**: langgraph + brief 03 (Python DSL,
no REST; LangGraph Server post-dates P-RC-9); cortex + brief
01 (OData-style REST vs v2 plain FastAPI); sint-protocol +
brief 02 (JSON-RPC over gRPC vs REST + JSON); crewAI + brief
05 (CLI-only); MetaGPT + brief 04 (Flask project-scoped, no
FastAPI patterns); autogen + brief 06 (WebSocket-only; Group
A SSE already covers streaming).

**Net adoption for P9.7: NONE.** All design inputs come
from the existing P-RC-3 ``orgs_v2.py`` +
``orgs_v2_stream.py`` patterns (Group A as in-repo
template), the P-RC-7..P-RC-9 internal precedents (FastAPI
``Depends`` + ``HTTPException`` + ``TestClient`` contract
tests), and the P9.1-P9.6 subsystem Protocols. FastAPI is
industry standard; the external research corpus offers
nothing superior. NIT-E-1 satisfied with all 12 items
explicitly rejected.

## 12. Cross-references + HARD STOP

PLAN section 4 P9.7 (charter row this expands);
``gates/G-RC-9.6.md`` (prior mini-gate; 6/6 sentinels);
``Q_DECISIONS.md`` Q-B (P-RC-9) ACCEPTED (b) 410 shim;
ADR-0011 (no new Protocols per R4), ADR-0012 (v1 delete
waits for P9.9), ADR-0013 (perf_counter SLA extended to
REST, ~5 cases), ADR-0014 (budget precedent; ADR-0015 NOT
written this round per section 2);
``src/openakita/runtime/orgs/{runtime, manager,
command_service, blackboard, project_store,
node_scheduler}.py`` (six subsystems P9.7 wires REST to).

**HARD STOP**: planning round only. P9.7alpha-1 is NOT
started in this commit. Next agent run, on operator signal,
opens P9.7alpha-1 (Group A reconciliation docs commit).
Until then, repository remains at HEAD ``89703a28`` plus
this charter + ledger row; ``git diff 89703a28..HEAD --
src/openakita/ tests/`` continues to return empty bytes.
**P-RC-9 status after this commit**: 6/6 subsystem rewrites
complete (P9.1-P9.6 closed); P9.7 charter LANDED;
P9.7alpha-1 NOT started; P9.8 / P9.9 / P9.10 unscheduled.