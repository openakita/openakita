# P-RC-9 P9.7 Endpoint Inventory (P9.7a-1, R2 recon)

**Status: RECON, DOCS-ONLY.** Catalogue of every REST endpoint
exposed under ``src/openakita/api/routes/orgs*.py`` at HEAD
``096a5571``. Counts supersede the approximate numbers in
``P-RC-9-P9.7-CHARTER.md`` section 1 ("v1 86 / v2 9 / mint ~80")
with measured-on-HEAD figures ("v1 89 / v2 9 / mint 83"). All
classification follows charter section 1.1 / 1.2 / 1.3.

* **Charter**: ``docs/revamp/P-RC-9-P9.7-CHARTER.md`` sec 1 + 4 + 8
* **ADRs cited**: ADR-0011 (``docs/revamp/P-RC-9-PLAN.md`` sec
  725-745; subsystem decomposition), ADR-0012 (PLAN sec
  746-...; ``orgs/`` deletion strategy)
* **Decisions ledger**: ``docs/revamp/P-RC-9-P9.7-DECISIONS.md``
  (D-1 R3 LOCKED + D-2/D-3/D-4)

## 1. Source surface as measured on HEAD ``096a5571``

Scanner: ``Select-String -Path src/openakita/api/routes/orgs*.py
-Pattern '^\s*@router\.(get|post|put|delete|patch|websocket)'``.

| file | LOC (approx) | endpoints | source phase |
|---|--:|--:|---|
| ``api/routes/orgs.py`` (v1) | ~2 533 | **89** | pre-P-RC-9 |
| ``api/routes/orgs_v2.py`` | 337 | **8** | P-RC-3 |
| ``api/routes/orgs_v2_stream.py`` | 139 | **1** SSE | P-RC-3 |
| **Total measured surface** | ~3 009 | **98** | -- |

**Delta vs charter section 1.** Charter claimed v1 = 86;
re-scan on HEAD ``096a5571`` returns 89. Diff
``89703a28..096a5571 -- src/openakita/api/routes/orgs*.py``
is empty (no v1 commits since charter authorship), so the
charter figure was an under-count, not a missing landing.
P9.7 mint target after Group C pruning becomes **89 - 6 = 83**
(not 80). G-RC-9.7 LOC budget unchanged because per-endpoint
average (charter section 2: ~18 LOC) absorbs +3 endpoints
inside ADR-0014's ~10 % tolerance.

## 2. Group A -- already-v2 (9; P-RC-3 vintage)

Disjoint data model (``runtime.orgs.JsonOrgStore`` over the
``OrgV2`` template schema) from the P9.5 ``OrgManager``
``Organization`` model that the v1 + Group B mint will use.
Path collision at ``/api/v2/orgs[/{id}]`` triggers D-1 (see
DECISIONS.md): Group A relocates to ``/api/v2/orgs-spec/``
with 308 redirect shims at the original paths through
v2.0.x; P9.7 mint takes ``/api/v2/orgs/...``.

| # | method | path | file:line | wires to |
|---:|---|---|---|---|
| A1 | GET    | ``/templates``                      | orgs_v2.py:162 | ``GLOBAL_REGISTRY.list()`` |
| A2 | GET    | ``/templates/{template_id}``        | orgs_v2.py:176 | ``GLOBAL_REGISTRY.get(template_id)`` |
| A3 | POST   | ``/templates/{template_id}/instantiate`` | orgs_v2.py:198 | template -> ``OrgV2`` (not persisted) |
| A4 | GET    | ``""`` (list)                       | orgs_v2.py:264 | ``get_default_store().list()`` |
| A5 | POST   | ``""`` (create)                     | orgs_v2.py:272 | ``get_default_store().save(...)`` |
| A6 | GET    | ``/{org_id}``                       | orgs_v2.py:295 | ``get_default_store().get(org_id)`` |
| A7 | PATCH  | ``/{org_id}``                       | orgs_v2.py:308 | ``get_default_store().update(...)`` |
| A8 | DELETE | ``/{org_id}``                       | orgs_v2.py:323 | ``get_default_store().delete(...)`` |
| A9 | GET    | ``/{org_id}/stream``                | orgs_v2_stream.py:115 | SSE: ``supervisor`` progress feed |

Count = 9; matches charter section 1.1. After D-1 R3 lands
(P9.7a-2), every path above changes prefix
``/api/v2/orgs[/...]`` -> ``/api/v2/orgs-spec[/...]``; 308
Permanent Redirect shims keep the originals reachable for
the v2.0.x line.

## 3. Group B -- v1 endpoints needing v2 replacement (83)

89 v1 minus 6 Group C = **83**. Cluster table preserves
charter section 1.2 12-group structure but reconciles
counts (+3 endpoints distributed across runtime/projects).
Every cluster wires through one or two ADR-0011 subsystems
(P9.1-P9.6) -- no subsystem appears twice in any cell.

### 3.1 Org CRUD + lifecycle + templates (17)

| # | method | path | wires to |
|---:|---|---|---|
| B1  | GET    | ``""``                            | OrgManager.list_orgs |
| B2  | POST   | ``""``                            | OrgManager.create_org |
| B3  | GET    | ``/avatar-presets``               | OrgManager.list_avatar_presets |
| B4  | POST   | ``/avatars/upload``               | OrgManager.upload_avatar |
| B5  | GET    | ``/templates``                    | OrgManager.list_templates |
| B6  | GET    | ``/plugin-workbench-templates``   | OrgManager.list_workbench_templates |
| B7  | GET    | ``/templates/{template_id}``      | OrgManager.get_template |
| B8  | POST   | ``/from-template``                | OrgManager.create_from_template |
| B9  | POST   | ``/import``                       | OrgManager.import_org |
| B10 | GET    | ``/{org_id}``                     | OrgManager.get_org |
| B11 | PUT    | ``/{org_id}``                     | OrgManager.update_org |
| B12 | DELETE | ``/{org_id}``                     | OrgManager.delete_org |
| B13 | POST   | ``/{org_id}/duplicate``           | OrgManager.duplicate_org |
| B14 | POST   | ``/{org_id}/archive``             | OrgManager.archive_org |
| B15 | POST   | ``/{org_id}/unarchive``           | OrgManager.unarchive_org |
| B16 | POST   | ``/{org_id}/save-as-template``    | OrgManager.save_as_template |
| B17 | POST   | ``/{org_id}/export``              | OrgManager.export_org |

### 3.2 Node lifecycle + identity + mcp + schedules (16)

| # | method | path | wires to |
|---:|---|---|---|
| B18 | GET    | ``/{org_id}/nodes/{node_id}/schedules``                  | NodeScheduler.list_for_node |
| B19 | POST   | ``/{org_id}/nodes/{node_id}/schedules``                  | NodeScheduler.create |
| B20 | PUT    | ``/{org_id}/nodes/{node_id}/schedules/{schedule_id}``    | NodeScheduler.update |
| B21 | DELETE | ``/{org_id}/nodes/{node_id}/schedules/{schedule_id}``    | NodeScheduler.delete |
| B22 | GET    | ``/{org_id}/nodes/{node_id}/identity``                   | OrgManager.get_node_identity |
| B23 | PUT    | ``/{org_id}/nodes/{node_id}/identity``                   | OrgManager.update_node_identity |
| B24 | GET    | ``/{org_id}/nodes/{node_id}/mcp``                        | OrgManager.get_node_mcp |
| B25 | PUT    | ``/{org_id}/nodes/{node_id}/mcp``                        | OrgManager.update_node_mcp |
| B26 | POST   | ``/{org_id}/nodes/{node_id}/freeze``                     | OrgRuntime.NodeStatusController.freeze |
| B27 | POST   | ``/{org_id}/nodes/{node_id}/unfreeze``                   | OrgRuntime.NodeStatusController.unfreeze |
| B28 | POST   | ``/{org_id}/nodes/{node_id}/offline``                    | OrgRuntime.NodeStatusController.offline |
| B29 | POST   | ``/{org_id}/nodes/{node_id}/online``                     | OrgRuntime.NodeStatusController.online |
| B30 | DELETE | ``/{org_id}/nodes/{node_id}/dismiss``                    | OrgManager.dismiss_node |
| B31 | GET    | ``/{org_id}/nodes/{node_id}/thinking``                   | OrgRuntime.get_node_thinking |
| B32 | GET    | ``/{org_id}/nodes/{node_id}/prompt-preview``             | OrgRuntime.preview_node_prompt |
| B33 | GET    | ``/{org_id}/nodes/{node_id}/status``                     | OrgRuntime.get_node_status |

### 3.3 Runtime control + Commands + Broadcast (8)

| # | method | path | wires to |
|---:|---|---|---|
| B34 | POST   | ``/{org_id}/start``                            | OrgRuntime.OrgLifecycleManager.start_org |
| B35 | POST   | ``/{org_id}/stop``                             | OrgRuntime.OrgLifecycleManager.stop_org |
| B36 | POST   | ``/{org_id}/pause``                            | OrgRuntime.OrgLifecycleManager.pause_org |
| B37 | POST   | ``/{org_id}/resume``                           | OrgRuntime.OrgLifecycleManager.resume_org |
| B38 | POST   | ``/{org_id}/command``                          | OrgCommandService.submit |
| B39 | GET    | ``/{org_id}/commands/{command_id}``            | OrgCommandService.get_command |
| B40 | POST   | ``/{org_id}/commands/{command_id}/cancel``     | OrgCommandService.cancel + OrgRuntime.cancel_user_command |
| B41 | POST   | ``/{org_id}/broadcast``                        | OrgRuntime.CommandDispatchManager.broadcast |

### 3.4 Memory + Events + Activity + Messages + audit + Policies (12)

| # | method | path | wires to |
|---:|---|---|---|
| B42 | GET    | ``/{org_id}/memory``                  | OrgBlackboard.read |
| B43 | POST   | ``/{org_id}/memory``                  | OrgBlackboard.write |
| B44 | DELETE | ``/{org_id}/memory/{memory_id}``      | OrgBlackboard.delete |
| B45 | GET    | ``/{org_id}/events``                  | OrgRuntime.event_store.query |
| B46 | GET    | ``/{org_id}/activity``                | OrgRuntime.event_store.activity_view |
| B47 | GET    | ``/{org_id}/messages``                | OrgRuntime.NodeMessageRouter.list_messages |
| B48 | GET    | ``/{org_id}/audit-log``               | OrgRuntime.event_store.audit |
| B49 | GET    | ``/{org_id}/policies``                | OrgManager.list_policies |
| B50 | GET    | ``/{org_id}/policies/search``         | OrgManager.search_policies |
| B51 | GET    | ``/{org_id}/policies/{filename}``     | OrgManager.read_policy |
| B52 | PUT    | ``/{org_id}/policies/{filename}``     | OrgManager.write_policy |
| B53 | DELETE | ``/{org_id}/policies/{filename}``     | OrgManager.delete_policy |

### 3.5 Inbox + Scaling + Reports + Stats + Status (14)

| # | method | path | wires to |
|---:|---|---|---|
| B54 | GET    | ``/{org_id}/inbox``                          | OrgRuntime.get_inbox.list |
| B55 | POST   | ``/{org_id}/inbox/{msg_id}/read``            | OrgRuntime.get_inbox.mark_read |
| B56 | POST   | ``/{org_id}/inbox/read-all``                 | OrgRuntime.get_inbox.mark_all_read |
| B57 | POST   | ``/{org_id}/inbox/{msg_id}/resolve``         | OrgRuntime.get_inbox.resolve |
| B58 | GET    | ``/{org_id}/scaling/requests``               | OrgManager.list_scaling_requests |
| B59 | POST   | ``/{org_id}/scaling/{request_id}/approve``   | OrgManager.approve_scaling |
| B60 | POST   | ``/{org_id}/scaling/{request_id}/reject``    | OrgManager.reject_scaling |
| B61 | POST   | ``/{org_id}/scale/clone``                    | OrgManager.scale_clone |
| B62 | POST   | ``/{org_id}/scale/recruit``                  | OrgManager.scale_recruit |
| B63 | GET    | ``/{org_id}/status``                         | OrgRuntime.get_status_snapshot |
| B64 | GET    | ``/{org_id}/stats``                          | OrgManager.get_stats |
| B65 | GET    | ``/{org_id}/reports``                        | OrgManager.list_reports |
| B66 | GET    | ``/{org_id}/reports/summary``                | OrgManager.get_report_summary |
| B67 | POST   | ``/{org_id}/reports/generate``               | OrgManager.generate_report |

### 3.6 Projects + tasks (16)

| # | method | path | wires to |
|---:|---|---|---|
| B68 | GET    | ``/{org_id}/projects``                                                | ProjectStore.list_projects |
| B69 | POST   | ``/{org_id}/projects``                                                | ProjectStore.create_project |
| B70 | GET    | ``/{org_id}/projects/{project_id}``                                   | ProjectStore.get_project |
| B71 | PUT    | ``/{org_id}/projects/{project_id}``                                   | ProjectStore.update_project |
| B72 | DELETE | ``/{org_id}/projects/{project_id}``                                   | ProjectStore.delete_project |
| B73 | POST   | ``/{org_id}/projects/{project_id}/tasks``                             | ProjectStore.create_task |
| B74 | PUT    | ``/{org_id}/projects/{project_id}/tasks/{task_id}``                   | ProjectStore.update_task |
| B75 | DELETE | ``/{org_id}/projects/{project_id}/tasks/{task_id}``                   | ProjectStore.delete_task |
| B76 | POST   | ``/{org_id}/projects/{project_id}/tasks/{task_id}/dispatch``          | ProjectStore.get_task + OrgCommandService.submit |
| B77 | POST   | ``/{org_id}/projects/{project_id}/tasks/{task_id}/cancel``            | ProjectStore.cancel_task |
| B78 | GET    | ``/{org_id}/tasks``                                                   | ProjectStore.list_tasks |
| B79 | GET    | ``/{org_id}/tasks/{task_id}``                                         | ProjectStore.get_task |
| B80 | GET    | ``/{org_id}/tasks/{task_id}/tree``                                    | ProjectStore.get_task_tree |
| B81 | GET    | ``/{org_id}/tasks/{task_id}/timeline``                                | ProjectStore.get_task_timeline |
| B82 | GET    | ``/{org_id}/nodes/{node_id}/tasks``                                   | ProjectStore.list_tasks_for_node |
| B83 | GET    | ``/{org_id}/nodes/{node_id}/active-plan``                             | ProjectStore.get_active_plan |

**Cluster totals**: 17 + 16 + 8 + 12 + 14 + 16 = **83** =
charter section 1.2 figure +3 (charter ~80 was anchored on
v1=86; corrected to 83 on v1=89). G-RC-9.7 gate criterion 1
(charter section 9) reads "all 83 v2 endpoints have >= 1
contract test", updated from the charter's "~80".

## 4. Group C -- v1 endpoints retired (6)

Stay live in v1 ``orgs.py`` until P9.9 per ADR-0012 + Q-B
(``Q_DECISIONS.md``); become 410 Gone after the 1-release
shim. Operator may reclassify any row to Group B at
P9.7a-1 (non-destructive: v1 keeps serving).

| # | method | path | rationale |
|---:|---|---|---|
| C1 | POST | ``/{org_id}/reset``                                              | replaced by stop + delete + create-from-template (charter sec 1.3) |
| C2 | POST | ``/{org_id}/heartbeat/trigger``                                  | debug-only; v2 OrgRuntime fires autonomously |
| C3 | POST | ``/{org_id}/standup/trigger``                                    | debug-only; v2 OrgRuntime fires autonomously |
| C4 | POST | ``/{org_id}/nodes/{node_id}/schedules/{schedule_id}/trigger``    | debug-only; v2 NodeScheduler fires autonomously |
| C5 | GET  | ``/{org_id}/events/replay``                                      | replaced by SSE replay-from-checkpoint (Group A A9) |
| C6 | POST | ``/{org_id}/im-reply``                                           | routes via ``channels/gateway.py`` directly |

Count = 6; matches charter section 1.3.

## 5. Subsystem coverage matrix

Each cell shows the number of Group B endpoints that the
corresponding ADR-0011 P9.1-P9.6 subsystem will serve.
Endpoint can wire to >= 2 subsystems (e.g. B40 cancel ->
OrgCommandService + OrgRuntime), so row totals exceed 83.

| subsystem (ADR-0011) | source phase | Group B endpoints | clusters |
|---|---|--:|---|
| OrgBlackboard         | P9.1 | 3  | 3.4 (memory) |
| ProjectStore          | P9.2 | 16 | 3.6 (projects + tasks) |
| NodeScheduler         | P9.3 | 4  | 3.2 (node schedules) |
| OrgCommandService     | P9.4 | 4  | 3.3 (commands) + 3.6 (dispatch / cancel) |
| OrgManager            | P9.5 | 33 | 3.1 (CRUD/templates) + 3.2 (node identity/mcp) + 3.4 (policies) + 3.5 (scaling/reports/stats) |
| OrgRuntime            | P9.6 | 23 | 3.2 (node controllers/status) + 3.3 (lifecycle/broadcast) + 3.4 (events/activity/messages/audit) + 3.5 (inbox/status snapshot) |

Group A (9) is **out of scope** for this matrix -- after
D-1 R3 lands those endpoints live under ``/api/v2/orgs-spec/``
backed by ``runtime.orgs.JsonOrgStore``, which is disjoint
from the 6 subsystems above.

## 6. HARD STOP + cross references

P9.7a-1 is **docs-only**. ``git diff 096a5571..HEAD --
src/openakita/ tests/ apps/`` returns empty bytes both
before and after this commit (strict additive invariant
enforced).

P9.7a-2 (Pydantic models + router skeleton + 308 redirect
shim) is **NOT started** this turn; that lands in the next
agent run on operator signal per charter section 3
"P9.7a-2 ~270 LOC ``orgs_v2_models.py``".

**See also**:

* ``docs/revamp/P-RC-9-P9.7-CHARTER.md`` sec 1 (Group
  A/B/C classification), sec 4 (subsystem wiring
  representative table), sec 8 (R1-R5 risks)
* ``docs/revamp/P-RC-9-P9.7-DECISIONS.md`` (D-1 R3
  LOCKED + D-2/D-3/D-4)
* ``docs/revamp/P-RC-9-PLAN.md`` sec 4 (P9.7 charter row),
  ADR-0011 (sec 725), ADR-0012 (sec 746)
* ``docs/revamp/PROGRESS_LEDGER_P9.md`` (P9.7a-1 row)
