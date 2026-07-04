# P-RC-9 P9.8 Caller Inventory (P9.8alpha-1)

**Status: RECON, DOCS-ONLY.** Catalogue of every HTTP caller
site touched by P9.8 caller migration. Measured on HEAD
``95b9f9b6`` (P9.8 charter just signed off). Mirrors the
P9.7a-1 endpoint inventory layout; counts supersede the
approximate figures in ``P-RC-9-P9.8-CHARTER.md`` sec 1.1 /
4 / R5 with file:line precision.

* **Charter**: ``docs/revamp/P-RC-9-P9.8-CHARTER.md`` sec 1, 3, 4, 5, 7, 9
* **Sibling**: ``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md``
* **ADRs cited**: ADR-0011 (no new Protocol; inventory is docs); ADR-0012 (308 shim retirement deferred per charter sec 8)
* **Scanner**: ``git grep -n "/api/(v2/)?orgs" -- <tree>`` reproduced via Python (PowerShell CJK pipe noise +/-2; Python authoritative)

## 1. Frontend v1 callers -- 62 hits across 12 files

Measured: **62** ``/api/orgs/`` textual matches; **+2** vs
charter sec R5 "60" (drift is in ``OrgChatPanel.tsx``
JSDoc/comments; CJK lines confuse PowerShell pipe -- zero
functional impact). Of the 62: **54** HTTP call sites (gamma-*
swap targets), **4** TS module imports of ``../api/orgs`` (NO-OP
-- TS file, not HTTP path), **4** JSDoc / ``//`` comments
(cosmetic text update only).

Classification per charter sec 1.1 + sec 7:

* **B = needs swap** (51 HTTP sites): ``/api/orgs/...`` -> ``/api/v2/orgs/...`` (B1-B83 wires per ``P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` sec 3)
* **C = delete-with-v1 in P9.9** (3 HTTP sites): ``reset``, ``heartbeat/trigger``, ``standup/trigger`` (C1/C2/C3 from sibling sec 4)

### 1.1 Per-file roll-up

| file (under ``apps/setup-center/src/``) | hits | HTTP | import | comment | swap class |
|---|--:|--:|--:|--:|---|
| ``views/OrgEditorView.tsx``                                | 20 | 19 | 1 | 0 | B (17) + C (2) |
| ``components/OrgProjectBoard.tsx``                         | 11 | 11 | 0 | 0 | B |
| ``components/OrgChatPanel.tsx``                            |  9 |  6 | 0 | 3 | B |
| ``components/OrgMonitorPanel.tsx``                         |  5 |  5 | 0 | 0 | B |
| ``components/OrgInboxSidebar.tsx``                         |  4 |  4 | 0 | 0 | B |
| ``views/PixelOfficeView.tsx``                              |  3 |  3 | 0 | 0 | B |
| ``components/OrgBlackboardPanel.tsx``                      |  2 |  2 | 0 | 0 | B |
| ``components/WorkbenchNodePicker.tsx``                     |  2 |  1 | 0 | 1 | B |
| ``components/__tests__/TemplatePickerDrawer.test.tsx``     |  2 |  0 | 2 | 0 | no-op |
| ``views/ChatView.tsx``                                     |  2 |  2 | 0 | 0 | B |
| ``components/OrgDashboard.tsx``                            |  1 |  1 | 0 | 0 | B |
| ``components/TemplatePickerDrawer.tsx``                    |  1 |  0 | 1 | 0 | no-op |
| **Total**                                                  | **62** | **54** | **4** | **4** | -- |

### 1.2 ``views/OrgEditorView.tsx`` (20 hits; top file)

| line | method | v1 path literal | v2 target | subsystem |
|---:|---|---|---|---|
| 73   | -      | ``import ... from "../api/orgs"``               | -- (TS import; no edit)    | -- |
| 843  | GET    | ``/api/orgs``                                   | ``/api/v2/orgs``           | OrgManager.list_orgs |
| 857  | GET    | ``/api/orgs/templates``                         | ``/api/v2/orgs/templates`` | OrgManager.list_templates |
| 869  | GET    | ``/api/orgs/${id}``                             | swap                       | OrgManager.get_org |
| 1043 | POST   | ``/api/orgs/${id}/start``                       | swap                       | OrgRuntime.OrgLifecycleManager.start_org |
| 1066 | POST   | ``/api/orgs/${id}/stop``                        | swap                       | OrgRuntime.OrgLifecycleManager.stop_org |
| 1105 | POST   | ``/api/orgs/${id}/export``                      | swap                       | OrgManager.export_org |
| 1110 | POST   | ``/api/orgs/${id}/export``                      | swap (2nd reference)       | OrgManager.export_org |
| 1130 | POST   | ``/api/orgs/import``                            | swap                       | OrgManager.import_org |
| 1148 | POST   | ``/api/orgs/${id}/reset``                       | -- (C1; leave for P9.9)    | OrgManager.reset_org (deprecated) |
| 1239 | PUT    | ``/api/orgs/${id}``                             | swap + verb to PATCH (R2)  | OrgManager.update_org -> OrgPatch |
| 1291 | POST   | ``/api/orgs``                                   | swap                       | OrgManager.create_org |
| 1322 | POST   | ``/api/orgs/from-template``                     | swap                       | OrgManager.create_from_template |
| 1388 | DELETE | ``/api/orgs/${id}``                             | swap                       | OrgManager.delete_org |
| 1609 | GET    | ``/api/orgs/${id}/stats``                       | swap                       | OrgManager.get_stats |
| 1750 | POST   | ``/api/orgs/${id}/nodes/${nid}/unfreeze``       | swap                       | OrgRuntime.NodeStatusController.unfreeze |
| 3824 | POST   | ``/api/orgs/avatars/upload``                    | swap                       | OrgManager.upload_avatar |
| 4101 | GET    | ``/api/orgs/${id}/nodes/${nid}/prompt-preview`` | swap                       | OrgRuntime.preview_node_prompt |
| 5343 | POST   | ``/api/orgs/${id}/heartbeat/trigger``           | -- (C2; leave for P9.9)    | (debug-only) |
| 5346 | POST   | ``/api/orgs/${id}/standup/trigger``             | -- (C3; leave for P9.9)    | (debug-only) |

Net: **17 B swaps** (one ``PUT`` -> ``PATCH`` verb change at
line 1239 per R2 / Pydantic ``OrgPatch``); **3 C paths left**
in place for P9.9 v1 deletion.

### 1.3 ``components/OrgProjectBoard.tsx`` (11 hits, all B)

| line | method | v1 path | subsystem |
|---:|---|---|---|
| 139 | GET    | ``/${id}/tasks/${tid}``                              | ProjectStore.get_task |
| 140 | GET    | ``/${id}/tasks/${tid}/timeline``                     | ProjectStore.get_task_timeline |
| 164 | GET    | ``/${id}/projects``                                  | ProjectStore.list_projects |
| 269 | PUT    | ``/${id}/projects/${pid}``                           | ProjectStore.update_project |
| 270 | POST   | ``/${id}/projects``                                  | ProjectStore.create_project |
| 290 | POST   | ``/${id}/projects/${pid}/tasks``                     | ProjectStore.create_task |
| 302 | DELETE | ``/${id}/projects/${pid}``                           | ProjectStore.delete_project |
| 310 | PUT    | ``/${id}/projects/${pid}/tasks/${tid}``              | ProjectStore.update_task |
| 320 | DELETE | ``/${id}/projects/${pid}/tasks/${tid}``              | ProjectStore.delete_task |
| 337 | POST   | ``/${id}/projects/${pid}/tasks/${tid}/dispatch``     | ProjectStore.get_task + OrgCommandService.submit |
| 346 | POST   | ``/${id}/projects/${pid}/tasks/${tid}/cancel``       | ProjectStore.cancel_task |

All 11 swap from ``/api/orgs`` to ``/api/v2/orgs`` (same suffix).

### 1.4 Remaining 10 files (compact)

| file | line | method | v1 path tail | subsystem / note |
|---|--:|---|---|---|
| ``components/OrgChatPanel.tsx``           | 105   | JSDoc   | ``/api/orgs/.../activity`` (legacy WS context) | text update |
| ``components/OrgChatPanel.tsx``           | 159   | comment | ``/api/orgs/{org}/activity``                  | text update |
| ``components/OrgChatPanel.tsx``           | 479   | comment | ``/api/orgs/{org}/activity``                  | text update |
| ``components/OrgChatPanel.tsx``           | 492   | GET     | ``/${id}/activity?limit=...``                 | OrgRuntime.event_store.activity_view |
| ``components/OrgChatPanel.tsx``           | 582   | GET     | ``/${id}/activity?limit=...``                 | (same) |
| ``components/OrgChatPanel.tsx``           | 685   | GET     | ``/${id}/commands/${cid}``                    | OrgCommandService.get_command |
| ``components/OrgChatPanel.tsx``           | 792   | POST    | ``/${id}/commands/${cid}/cancel``             | OrgCommandService.cancel |
| ``components/OrgChatPanel.tsx``           | 1203  | POST    | ``/${id}/command``                            | OrgCommandService.submit |
| ``components/OrgChatPanel.tsx``           | 1252  | GET     | ``/${id}/commands/${cid}``                    | OrgCommandService.get_command |
| ``components/OrgMonitorPanel.tsx``        | 176   | GET     | ``/${id}/events?actor=...``                   | OrgRuntime.event_store.query |
| ``components/OrgMonitorPanel.tsx``        | 177   | GET     | ``/${id}/nodes/${nid}/schedules``             | NodeScheduler.list_for_node |
| ``components/OrgMonitorPanel.tsx``        | 178   | GET     | ``/${id}/nodes/${nid}/thinking?limit=...``    | OrgRuntime.get_node_thinking |
| ``components/OrgMonitorPanel.tsx``        | 206   | GET     | ``/${id}/nodes/${nid}/tasks``                 | ProjectStore.list_tasks_for_node |
| ``components/OrgMonitorPanel.tsx``        | 207   | GET     | ``/${id}/nodes/${nid}/active-plan``           | ProjectStore.get_active_plan |
| ``components/OrgInboxSidebar.tsx``        | 97    | GET     | ``/${id}/inbox?...``                          | OrgRuntime.get_inbox.list |
| ``components/OrgInboxSidebar.tsx``        | 123   | POST    | ``/${id}/inbox/${msgId}/read``                | OrgRuntime.get_inbox.mark_read |
| ``components/OrgInboxSidebar.tsx``        | 133   | POST    | ``/${id}/inbox/read-all``                     | OrgRuntime.get_inbox.mark_all_read |
| ``components/OrgInboxSidebar.tsx``        | 143   | POST    | ``/${id}/inbox/${msgId}/resolve``             | OrgRuntime.get_inbox.resolve |
| ``views/PixelOfficeView.tsx``             | 77    | GET     | ``/api/orgs``                                 | OrgManager.list_orgs |
| ``views/PixelOfficeView.tsx``             | 109   | GET     | ``/${id}``                                    | OrgManager.get_org |
| ``views/PixelOfficeView.tsx``             | 227   | POST    | ``/${id}/command``                            | OrgCommandService.submit |
| ``components/OrgBlackboardPanel.tsx``     | 41    | GET     | ``/${id}/memory?...``                         | OrgBlackboard.read |
| ``components/OrgBlackboardPanel.tsx``     | 77    | DELETE  | ``/${id}/memory/${eid}``                      | OrgBlackboard.delete |
| ``components/WorkbenchNodePicker.tsx``    | 5     | JSDoc   | ``/api/orgs/plugin-workbench-templates``      | text update |
| ``components/WorkbenchNodePicker.tsx``    | 84    | GET     | ``/plugin-workbench-templates``               | OrgManager.list_workbench_templates |
| ``views/ChatView.tsx``                    | 1046  | GET     | ``/api/orgs``                                 | OrgManager.list_orgs |
| ``views/ChatView.tsx``                    | 3832  | POST    | ``/${id}/commands/${cid}/cancel``             | OrgCommandService.cancel |
| ``components/OrgDashboard.tsx``           | 176   | GET     | ``/${id}/stats``                              | OrgManager.get_stats |
| ``components/TemplatePickerDrawer.tsx``   | 39    | -       | ``from "../api/orgs"`` (TS module)            | NO-OP |
| ``components/__tests__/TemplatePickerDrawer.test.tsx`` | 7  | -    | ``vi.mock("../../api/orgs", ...)``            | NO-OP |
| ``components/__tests__/TemplatePickerDrawer.test.tsx`` | 47 | -    | ``import * as orgsApi from "../../api/orgs"`` | NO-OP |

All B-class HTTP sites swap prefix ``/api/orgs`` -> ``/api/v2/orgs``;
suffix unchanged.

## 2. Frontend v2 callers -- 17 hits across 6 files

Measured: **17** ``/api/v2/orgs/`` matches. Actionable Group A
constructions (charter D-2 + D-1 R3 LOCKED) are:

* ``api/orgs.ts``: **8** ``apiUrl(apiBase, "api", "v2", "orgs", ...)`` calls (lines 104, 108, 117, 127, 131, 135, 143, 152)
* ``api/v2Stream.ts``: **1** template literal at line 112 (SSE URL)

These 9 functional sites retarget ``orgs`` -> ``orgs-spec``
because P9.7a-2a relocated Group A endpoints to
``/api/v2/orgs-spec/``; the 308 shim keeps old paths reachable
through v2.0.x, but the canonical literal lives at ``orgs-spec``.

| file | line(s) | kind | content | action |
|---|---|---|---|---|
| ``api/orgs.ts``                         | 11-18      | JSDoc | endpoint table at top of module                                       | text update to ``-spec`` (8 lines) |
| ``api/orgs.ts``                         | 104..152   | code  | 8 ``apiUrl(...)`` Group A constructions                              | retarget arg ``"orgs"`` -> ``"orgs-spec"`` (8 lines) |
| ``api/v2Stream.ts``                     | 2          | JSDoc | mentions ``/api/v2/orgs/{id}/stream``                                | text update |
| ``api/v2Stream.ts``                     | 112        | code  | ``${apiBase}/api/v2/orgs/${...}/stream``                             | retarget to ``orgs-spec`` |
| ``api/__tests__/v2Stream.test.ts``      | 68         | test  | ``expect(... .url).toBe("/api/v2/orgs/.../stream")``                 | mock path -> ``orgs-spec`` |
| ``components/OrgChatPanel.tsx``         | 107, 409   | JSDoc | mentions ``/api/v2/orgs/{id}/stream``                                | text update (Group B-style note about SSE) |
| ``components/TemplatePickerDrawer.tsx`` | 5, 10      | JSDoc | mentions ``GET /api/v2/orgs/templates`` + ``POST /api/v2/orgs``      | text update |
| ``views/OrgEditorView.tsx``             | 1348, 1349 | comment | mentions ``/api/v2/orgs/templates/{id}/instantiate`` + ``/api/v2/orgs`` | text update |

Classification: all 9 functional sites are **Group A (rename
only)**; the remaining 8 textual hits are **comment / docstring
updates**. Per charter D-2 there are **0 Group B (path
unchanged)** v2 frontend callers today -- the P9.7 mint surface
is reached exclusively via the sec 1 v1->v2 swap.

## 3. Backend Python callers -- 5 process-internal imports

Per charter sec 4 + sec 5 R3: ``src/openakita/channels/``
contains **0** HTTP callers; every reference to the v1
subsystem is an in-process ``import`` riding the P9.9 deletion
/ rename sweep (**P9.9 scope, not P9.8**).

| file | line | import statement |
|---|---:|---|
| ``src/openakita/channels/gateway.py`` | 3182 | ``from openakita.orgs.command_service import get_command_service`` |
| ``src/openakita/channels/gateway.py`` | 3256 | ``from openakita.orgs.command_service import get_command_service`` |
| ``src/openakita/channels/gateway.py`` | 3335 | ``from openakita.orgs.command_service import get_command_service`` |
| ``src/openakita/channels/gateway.py`` | 3449 | ``from openakita.orgs.command_service import get_command_service`` |
| ``src/openakita/channels/gateway.py`` | 3739 | ``from openakita.orgs.command_service import (...)`` (multi-symbol) |

**Verdict: NO-OP in P9.8.** P9.9 rewrites target from
``openakita.orgs.command_service`` (v1) to
``openakita.runtime.orgs.command_service`` (P9.4 vintage)
alongside ``git rm -r src/openakita/orgs/``.

## 4. Test callers

### 4.1 v1 tests -- 56 hits in 5 files (delete-with-v1 in P9.9)

| file (``tests/orgs/``) | hits | role |
|---|--:|---|
| ``test_api.py``                       | 21 | direct v1-surface assertions |
| ``test_prompt_api_e2e.py``            | 27 | E2E v1 prompt + node identity |
| ``test_transparency_autonomy.py``     |  6 | v1 policy + autonomy gates |
| ``test_org_status_snapshot.py``       |  1 | v1 status snapshot smoke |
| ``test_runtime_deadlock_watchdog.py`` |  1 | v1 runtime watchdog smoke |
| **Total**                             | **56** | -- |

**P9.8 action: NONE.** Charter sec 1.2 + sec 4 anchor: these
tests assert the v1 contract directly and delete as a block in
P9.9; migrating to v2 would invent coverage already provided
by ``tests/api/contracts/`` (184 cases) and ``tests/parity/orgs/``
(P9.1-P9.6 parity slices).

### 4.2 v2 tests -- 463 hits across 4 subtrees (no action)

| subtree | hits | role |
|---|--:|---|
| ``tests/api/``         | 374 | contract suites + REST contract sentinel |
| ``tests/parity/``      |  84 | P-RC-9 parity slices |
| ``tests/integration/`` |   5 | end-to-end (including v2 IM canary) |
| ``tests/runtime/``     |   0 | -- |
| **Total**              | **463** | -- |

Specifically: ``tests/integration/test_v2_im_canary_e2e.py``
(lines 11, 117) still calls ``/api/v2/orgs/templates/{id}/instantiate``
(pre-P9.7a-2a Group A path). The 308 shim from
``api/routes/_orgs_v2_legacy_redirects.py`` keeps it green;
P9.7.nit-a restored this canary at HEAD ``652c8a71``. Per
charter sec 8 the canary remains valid as-is until shim
retirement (recommendation: defer to v2.1.0). **No P9.8 edit.**

### 4.3 Test fixture catch-up tied to gamma

* ``components/__tests__/TemplatePickerDrawer.test.tsx``
  (``vi.mock`` + ``import * as``) -- **NO edit**; the TS
  module path ``../../api/orgs`` resolves to the same file
  (which gamma-1 retargets internally).
* ``api/__tests__/v2Stream.test.ts:68`` -- **1-line edit
  in gamma-1**: mock URL string ``orgs`` -> ``orgs-spec``.

## 5. Docs callers -- 115 hits (out of scope)

Measured: ``docs/`` contains 34 ``/api/orgs/`` + 84
``/api/v2/orgs/`` = **115** combined references (vs charter
sec 4 "86" anchor; +29 drift from intervening P9.6 / P9.7
narrative additions). **Out of P9.8 swap scope** -- these are
descriptive narratives, not executable callers. Charter sec
1.2: "updated in narratives only when ledger / gate text adds
new context".

## 6. Scripts -- 0 hits

``git grep -nE "/api/(v2/)?orgs" -- "scripts/"`` returns 0
matches. Confirmed per charter sec 4. No action.

## 7. Swap plan + 8. Pydantic-TS drift

### 7.1 Per-file swap classification

| file | hits | LOC est | action | gamma slot |
|---|--:|--:|---|---|
| ``api/orgs.ts`` + ``api/v2Stream.ts`` + ``api/__tests__/v2Stream.test.ts`` | 8+2+1 | ~80 | Group A retarget (``orgs`` -> ``orgs-spec``) + docstring + test mock | **gamma-1** |
| ``views/OrgEditorView.tsx`` | 20 | ~120 | direct path swap (17 B); 3 C-paths left for P9.9; one PUT->PATCH verb tweak | **gamma-2** |
| ``components/OrgProjectBoard.tsx`` + ``components/OrgChatPanel.tsx`` | 11+9 | ~110 | direct path swap; OrgChatPanel = 3 comments + 6 HTTP | **gamma-3** |
| ``components/{OrgMonitorPanel,OrgInboxSidebar,OrgBlackboardPanel,OrgDashboard,WorkbenchNodePicker}.tsx`` + ``views/{ChatView,PixelOfficeView}.tsx`` | 19 | ~90 | direct path swap; smallest deltas | **gamma-4** |
| ``components/TemplatePickerDrawer.tsx`` + ``__tests__/TemplatePickerDrawer.test.tsx`` | 1+2 | 0 | NO-OP (TS module imports stay valid) | -- |

Net gamma: **~400 LOC** across **4 commits** (80 / 120 / 110 /
90). Each commit <= 350 soft cap. Charter sec 6 reserved
250-350; measured target ~400 (+14% drift inside ADR-0014
tolerance for "small wiring" phases; no charter amendment).

### 7.2 Method change (R2)

* ``views/OrgEditorView.tsx:1239`` -- ``PUT /api/orgs/${id}``
  (v1) -> ``PATCH /api/v2/orgs/${id}`` (Pydantic ``OrgPatch``
  partial body; sibling B11). gamma-2 swaps path **and** verb.

### 8. Pydantic-TS drift (R2; recorded for P-RC-10 OpenAPI codegen)

**``OrgWire`` (TS) vs ``Org`` (Pydantic)**: 5 field deltas --
TS has ``template_id``; Py has ``icon`` / ``core_business`` /
``workspace_dir`` / ``tags``; TS allows ``[key: string]: unknown``
extras vs Py ``extra="forbid"`` (potential 422 if stale extras
shipped); status is free string in TS vs ``OrgStatus`` enum in
Py; ``description`` nullability mismatch.

**``createOrg`` envelope (HIGH)**: TS posts ``{ org }`` wrapper
(orgs.ts:131); Pydantic ``OrgCreate`` expects bare body. P9.7
mint at ``/api/v2/orgs`` takes bare form; Group A at
``/api/v2/orgs-spec`` accepts wrapper. gamma-1 keeps wrapper
(Group A unchanged); gamma-2 line 1291 already sends raw
payload so swap aligns naturally.

**No TS counterpart**: ``Node`` / ``NodeRegister`` /
``NodeStatus``, ``CommandSubmit`` / ``CommandSnapshot`` /
``CancelRequest``, ``Project`` / ``ProjectCreate`` /
``ProjectPatch`` + 3 status enums -- frontend treats every
payload as ``Record<string, unknown>``. No TS interface to
drift against.

**Drift summary**: 1 endpoint with TS-vs-Pydantic divergence
(5 field deltas + 1 envelope); 15 endpoints with no TS
counterpart. **P9.8 action: NONE; P-RC-10 codegen TODO.**

## 9. gamma commit boundary proposal

| commit | files | hits | LOC est | rationale |
|---|---|--:|--:|---|
| **gamma-1** | ``api/orgs.ts`` + ``api/v2Stream.ts`` + ``api/__tests__/v2Stream.test.ts`` | 11 | ~80 | Group A retarget; SSE URL + test mock string; docstring fixup |
| **gamma-2** | ``views/OrgEditorView.tsx`` | 20 | ~120 | largest single file; 17 B swaps + 3 C leftovers + 1 PUT->PATCH |
| **gamma-3** | ``components/OrgProjectBoard.tsx`` + ``components/OrgChatPanel.tsx`` | 20 | ~110 | project board + chat panel cluster |
| **gamma-4** | ``components/{OrgMonitorPanel,OrgInboxSidebar,OrgBlackboardPanel,OrgDashboard,WorkbenchNodePicker}.tsx`` + ``views/{ChatView,PixelOfficeView}.tsx`` | 19 | ~90 | remaining 7 files; small per-file deltas |
| no-op | ``TemplatePickerDrawer.tsx`` + ``__tests__/TemplatePickerDrawer.test.tsx`` | 3 | 0 | TS module imports unchanged |
| **gamma total** | -- | **70** | **~400** | 4 commits; each <= 350 soft cap |

## 10. HARD STOP + cross references

P9.8alpha-1 is **docs-only**. ``git diff 95b9f9b6..HEAD --
src/openakita/ tests/ apps/`` returns empty bytes both before
and after this commit.

P9.8gamma-1 (first ``apps/`` edit -- ``api/orgs.ts`` Group A
retarget) is **NOT started** this turn; opens in the next agent
run on operator signal per charter sec 3 + sec 13.

**See also**: ``P-RC-9-P9.8-CHARTER.md`` sec 1/3/4/5/7/9; ``P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` (sibling template; sec 2 Group A, sec 3 Group B); ``P-RC-9-P9.7-DECISIONS.md`` D-1 R3 LOCKED + D-2; ``apps/setup-center/src/api/{orgs.ts,v2Stream.ts}`` (gamma-1 retarget targets); ``PROGRESS_LEDGER_P9.md`` (P9.8alpha-1 row).
