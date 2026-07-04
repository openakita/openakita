# P-RC-9 P9.7 Decisions log (P9.7a-1)

**Status: DOCS-ONLY DECISIONS.** Locks the four open
questions left by ``P-RC-9-P9.7-CHARTER.md`` before the
P9.7a-2 router scaffold lands.

* **Charter**: ``docs/revamp/P-RC-9-P9.7-CHARTER.md`` sec 1
  (R1 reconciliation), sec 8 R1 (path collision), sec 8 R2
  (frontend port-flip recon gap)
* **Inventory**: ``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md``
  (89 v1 + 9 Group A + 83 Group B + 6 Group C)
* **ADRs cited**: ADR-0011 (``P-RC-9-PLAN.md`` sec 725;
  subsystem decomposition + Protocol granularity ceiling),
  ADR-0012 (PLAN sec 746; no-shim under v1 + v1 delete
  waits for P9.9)

## D-1 Group A reconciliation -- R3 LOCKED

**Decision: R3 LOCKED.** Group A (9 P-RC-3 endpoints under
``/api/v2/orgs[/...]`` backed by ``runtime.orgs.JsonOrgStore``)
**relocates** to ``/api/v2/orgs-spec[/...]``; P9.7 mint
takes the original ``/api/v2/orgs[/...]`` namespace.

For the v2.0.x line every original Group A path keeps
serving via a **308 Permanent Redirect** shim that points
at the corresponding ``/api/v2/orgs-spec/...`` target.
Shim cost ~30 LOC (one ``APIRouter`` with 9 ``add_api_route``
calls + a tiny helper that issues ``RedirectResponse(...,
status_code=308)``). Shim is removed in v2.1.0 per ADR-0012
(no-shim invariant relaxed only for the explicit redirect
window).

**Rejected alternatives (charter section 1.1):**

* **R1 (rename only, no shim)**: simpler than R3 by ~30 LOC
  but breaks every frontend call site (~5 call sites in
  ``apps/setup-center/src/api/orgs.ts`` + ``api/v2Stream.ts``
  + the test mocks) on the day the v2.0.0 tag ships.
  Rejected because the frontend team cannot land the
  rewiring in the same release as P9.7gamma-4 close.
* **R2 (``?model=`` query-param multiplex)**: breaks
  OpenAPI codegen because two response models share one
  ``operationId``; also pushes the disambiguation into
  every client. Rejected by charter section 1.1 R2.

**Wire-up checklist for P9.7a-2** (NOT executed this turn):

1. Add ``prefix="/api/v2/orgs-spec"`` to the existing
   ``orgs_v2.router`` (single-line change).
2. Add ``prefix="/api/v2/orgs-spec"`` to the
   ``orgs_v2_stream.router``.
3. New ``orgs_v2_redirects.py`` module: 9
   ``router.add_api_route(<old_path>, _redirect_to_spec,
   methods=[...], status_code=308)`` lines + the 3-line
   ``_redirect_to_spec`` helper. Total ~35 LOC.
4. Register the redirect router in ``api/app_factory.py``
   ahead of the P9.7 mint router so collisions resolve in
   favour of the 308 shim, not the mint.
5. Frontend rewire (deferred to P9.8): replace
   ``apiUrl(apiBase, "api", "v2", "orgs", ...)`` with
   ``apiUrl(apiBase, "api", "v2", "orgs-spec", ...)`` in
   ``apps/setup-center/src/api/orgs.ts`` +
   ``api/v2Stream.ts`` + 2 component test mocks. Total
   ~12 line edits.

**R3 LOCKED** -- this marker is the unambiguous lock
signal for the P9.7a-2 / a-3 / beta-* turns.

## D-2 Frontend config entry point -- recon outcome

**Decision: NO single ``config.ts`` exists; the
"API base" is computed per-request by ``httpApiBase()``
and passed as a prop (``apiBaseUrl`` / ``apiBase``) into
every component that talks to the backend.**

**Recon scan** (P9.7a-1, HEAD ``096a5571``):

* ``Test-Path apps/setup-center/src/config.ts`` -> **False**
  (the file PLAN sec 4 P9.7 + charter sec 8 R2 referenced
  does not exist).
* ``Get-ChildItem apps/setup-center/src/`` returns 14
  top-level ``.ts`` / ``.tsx`` files (``api.ts``,
  ``App.tsx``, ``AppContext.tsx``, ``constants.ts``,
  ``env.d.ts``, ``globals.css``, ``icons.tsx``,
  ``localFetch.ts``, ``main.tsx``, ``providers.ts``,
  ``streamEvents.ts``, ``styles.css``, ``theme.ts``,
  ``types.ts``, ``utils.ts``) + the ``api/``, ``hooks/``,
  ``components/``, ``views/``, ``platform/``, ``lib/``,
  ``i18n/``, ``utils/``, ``test/``, ``assets/`` subfolders.
* Group A traffic is centralised in
  ``apps/setup-center/src/api/orgs.ts`` (8 thin functions
  taking ``apiBase: string`` as first arg + composing the
  URL via ``apiUrl(apiBase, "api", "v2", "orgs", ...)``)
  and the SSE wrapper ``apps/setup-center/src/api/v2Stream.ts``.
* v1 traffic is **scattered** across ``views/`` +
  ``components/`` (e.g. ``OrgChatPanel.tsx``,
  ``OrgEditorView.tsx``, ``OrgProjectBoard.tsx``,
  ``OrgInboxSidebar.tsx``, ``OrgMonitorPanel.tsx``,
  ``OrgBlackboardPanel.tsx``, ``OrgDashboard.tsx``,
  ``ChatView.tsx``, ``PixelOfficeView.tsx``,
  ``WorkbenchNodePicker.tsx``), each calling
  ``safeFetch(`${apiBaseUrl}/api/orgs/...`)`` directly
  with ``apiBaseUrl`` taken from a parent prop.

**Implication for P9.7gamma-4 build-artifact check**: the
charter R2 build-artifact assertion ("``dist-web``
``BUILD_INFO.api_default == '/api/v2'``") still applies,
but the source of that constant is not a ``config.ts``
literal -- it lives wherever ``httpApiBase()`` reads its
default (``providers.ts`` candidate; final identification
deferred to P9.8 caller-migration when frontend rewiring
actually lands).

**P9.7a-1 itself does NOT edit any ``apps/`` file** per
the docs-only hard rule. The recon above is the deliverable.

## D-3 Pydantic request / response models layer -- fresh schemas/orgs_v2/

**Decision: P9.7a-2 ships fresh Pydantic shapes under
``src/openakita/api/schemas/orgs_v2/*.py``, not inline
inside ``orgs_v2.py`` or the v1 ``orgs.py`` module.**

Rationale (ADR-0011 layer separation):

* ADR-0011 separates Protocol contracts from the
  modules that consume them. Symmetric here: REST wire
  shapes belong in a ``schemas/`` namespace that contract
  tests can import without dragging in the FastAPI router.
* Charter section 2 budgets ~270 LOC for ~22 Pydantic
  shapes; splitting into ``schemas/orgs_v2/orgs.py`` /
  ``schemas/orgs_v2/nodes.py`` / ``schemas/orgs_v2/commands.py``
  / ``schemas/orgs_v2/projects.py`` / ``schemas/orgs_v2/__init__.py``
  keeps each sub-file under the ADR-0014 ~380 LOC WARN
  threshold and mirrors the section 3 cluster layout
  (org / node / runtime+command / projects).
* Re-using v1 ``orgs.py`` inline body shapes was
  considered and **rejected**: v1 returns
  ``dict[str, Any]`` directly from ``mgr.list_orgs()`` /
  ``runtime.get_status(...)`` without typed wire models,
  so there is nothing to import.

**No changes ship this turn.** ``src/openakita/api/schemas/``
remains untouched at HEAD ``096a5571``; the new
``orgs_v2/`` namespace is created by the P9.7a-2 commit.

## D-4 v2 auth dependency -- reuse v1 ``request.app.state`` pattern

**Decision: v2 endpoints reuse the v1 ``Depends``-free
``request.app.state`` access pattern.**

Evidence from ``src/openakita/api/routes/orgs.py``
(HEAD ``096a5571``): the file contains **zero**
``Depends(...)`` calls. Every endpoint takes a bare
``request: Request`` argument and obtains its subsystem
handles via tiny module-level helpers:

```python
def _get_manager(request: Request):
    mgr = getattr(request.app.state, "org_manager", None)
    if mgr is None:
        raise HTTPException(503, "OrgManager not initialized")
    return mgr


def _get_runtime(request: Request):
    rt = getattr(request.app.state, "org_runtime", None)
    ...
```

The v1 surface ships 89 endpoints with NO FastAPI
``Depends`` injection: ``app_factory.py`` hangs the
subsystems onto ``app.state`` at startup, and the
helpers above unwrap them per request.

P9.7a-3 (optional ~120 LOC, charter section 3) mirrors
this with a small ``_orgs_v2_deps.py`` module exposing
``get_org_manager(request)`` / ``get_org_runtime(request)``
/ ``get_command_service(request)`` /
``get_project_store(request)`` /
``get_blackboard(request)`` /
``get_node_scheduler(request)``. Six free-function
helpers, not ``Depends(...)`` factories: the seam stays
flat per charter section 8 R4 ("resist introducing
``RestAuthProtocol`` etc.").

**Rejected alternatives:**

* **D-4 alt-1 (FastAPI ``Depends(get_runtime)``)**:
  trivially correct but invents an injection pattern v1
  never used; complicates parity-style debugging where
  operators compare v1 vs v2 stack traces. Rejected
  because the win is style-only.
* **D-4 alt-2 (new ``RestAuthProtocol``)**: explicitly
  rejected by charter section 8 R4 ("Keep DI flat at 6
  factories"). Not pursued.

## HARD STOP + cross references

P9.7a-1 lands this DECISIONS log + the inventory
catalogue + 1 ledger row. **No source edits.** ``git
diff 096a5571..HEAD -- src/openakita/ tests/ apps/``
returns empty bytes; CI/lint/typing untouched.

P9.7a-2 (Pydantic models scaffold under
``schemas/orgs_v2/``) + the D-1 308 redirect shim are
**NOT started** this turn; they ship in the next agent
run on operator signal.

**See also:**

* ``docs/revamp/P-RC-9-P9.7-CHARTER.md`` sec 1.1 (R1/R2/R3),
  sec 3 (alpha phase breakdown), sec 8 (R1/R2 risks),
  sec 12 (HARD STOP)
* ``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` (89 v1
  + 9 Group A + 83 Group B + 6 Group C; subsystem coverage
  matrix)
* ``docs/revamp/P-RC-9-PLAN.md`` sec 4 (P9.7 charter row),
  ADR-0011 (sec 725), ADR-0012 (sec 746)
* ``docs/revamp/PROGRESS_LEDGER_P9.md`` (P9.7a-1 row in
  the P9.7a section)
