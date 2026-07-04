# ADR-0015 -- 308 shim retirement governance (P9.9)

- **Status**: Accepted
- **Date**: 2026-05-20
- **Phase**: P-RC-9 P9.9.adr (informs forthcoming P-RC-9-P9.9-CHARTER + v2.1.0 milestone)
- **Decision owner**: project owner (maintainer)
- **Implementer**: AI agent on ``revamp/v3-orgs``

## Context

P9.7a-2a (commit ``31332276``) relocated the P-RC-3 Group A v2
routers from ``/api/v2/orgs[/...]`` to ``/api/v2/orgs-spec[/...]``
and installed a thin 308 Permanent Redirect router
(``src/openakita/api/routes/_orgs_v2_legacy_redirects.py``,
**9 routes**) at the original ``/api/v2/orgs`` paths so existing
callers (frontend, IM channels, manual curl users, admin scripts,
webhooks) keep working through the v2.0.x release window.

The shim is binary: each route returns a 308 ``Response`` whose
``Location`` header points at the canonical ``/api/v2/orgs-spec``
twin. 308 (not 301/302) preserves HTTP method + request body so
POST / PATCH / DELETE round-trip end-to-end (RFC 7231 / RFC 7538).

The Q-B answer recorded in ``docs/revamp/Q_DECISIONS.md`` (P-RC-9
plan section 7, accepted 2026-05-19) reads literally:

> **(b) 1-release HTTP 410 Gone shim** -- hard-delete in v2.1.0

i.e. each major v1 -> v2 cutover gets exactly **one release window**
of compatibility, and the hard-delete moves to the **next** release.
Q-B was answered for the v1 ``api/routes/orgs.py`` 410 cutover, but
the same 1-release-window discipline applies symmetrically to the
P9.7 308 shim because both are backward-compat insurance layers
introduced in the v2.0 cycle.

Two consecutive audits independently raised when to retire the
9-route 308 shim:

* **G-RC-9.7** (P9.7 mint mini-gate) section 13 closing matrix
  flagged ``ADR-0015 NOT filed`` and listed shim retirement as
  the candidate trigger.
* **G-RC-9.8** (P9.8 caller-migration mini-gate) section 8 reads:
  ``Optional retirement of _orgs_v2_legacy_redirects.py (9 308
  shims; ~101 LOC) per P9.8 charter sec 8 recommendation -- defer
  to v2.1.0 to preserve the 1-release-window contract.`` Section
  13 concludes: ``P9.9 needs its own charter + ADR-0015 (308 shim
  retirement governance per Q-B ACCEPTED (b) single-window
  discipline).``

Both audits recommend **option (b)**: NO-OP the shim in P9.9,
retire it in v2.1.0. This ADR ratifies that recommendation.

## Decision

**Adopt option (b): retire the 308 shim at v2.1.0; P9.9 is a
documented NO-OP for ``_orgs_v2_legacy_redirects.py``.**

Concretely:

1. P9.9 (final P-RC-9 phase) deletes ``src/openakita/orgs/`` (v1
   subsystem), ``api/routes/orgs.py`` (v1 router; 410 Gone shim
   per Q-B), and ``tests/orgs/`` -- but leaves
   ``api/routes/_orgs_v2_legacy_redirects.py`` and its 9 routes
   **byte-level untouched**.
2. The forthcoming **P-RC-9-P9.9-CHARTER section 8** (separate
   task; explicitly out of scope for this ADR) carries this
   NO-OP as an explicit non-goal: charter must list the 9 shim
   route paths, cite this ADR, cite Q-B, and assert that
   sentinels #7 (REST contract / OpenAPI snapshot) and #8
   (frontend stale-path) continue to observe the shim at HEAD.
3. A **new v2.1.0 milestone task** owns the physical retirement:
   ``git rm src/openakita/api/routes/_orgs_v2_legacy_redirects.py``
   + drop its mount from ``src/openakita/api/server.py``
   + regenerate the OpenAPI snapshot at
   ``tests/parity/orgs/_openapi_snapshot.json`` to remove the 9
   shim routes (sentinel #7, ``test_rest_contract_sentinel.py``)
   + sweep sentinel #8 (``test_frontend_stale_paths_sentinel.py``)
   for any Group C / TS-import allowlist drift.

The shim therefore lives the lifetime of the v2.0.x release line
and dies cleanly at the v2.1.0 cut.

## Consequences

* **P9.9 (this release window)**: shim NO-OP. Charter section 8
  (forthcoming) documents the 9 routes, cites this ADR, and lists
  sentinels #7 + #8 as **unchanged**. The 410 shim under
  ``api/routes/orgs.py`` (separate, Q-B governed) is unaffected
  -- it lands in P9.9 as planned.
* **v2.1.0 milestone**: gains a single concrete task --
  *"Retire 308 shim ``_orgs_v2_legacy_redirects.py`` (9 routes)
  per ADR-0015"*. Scope: file deletion + ``server.py``
  registration drop + OpenAPI snapshot regeneration (9 fewer
  routes) + sentinel #8 sweep if Group C paths are impacted.
  Net delta at v2.1.0: approximately **-101 src LOC** + **-9
  OpenAPI snapshot entries**.
* **Caller contract**: any caller still hitting the original
  ``/api/v2/orgs/*`` Group A paths receives **HTTP 308** with
  ``Location: /api/v2/orgs-spec/*`` for the entire v2.0.x line;
  starting at v2.1.0 the same paths return **404 Not Found**
  (the routes disappear from the FastAPI registry; no extra 410
  wrapper -- the 308 itself was the migration signal). Callers
  that already migrated during v2.0.x -- including the P9.8
  frontend swap (``orgs.ts`` + ``v2Stream.ts`` + 11 view /
  component files) -- write directly to ``/api/v2/orgs-spec``
  and never touch the shim.
* **Q-B integrity preserved**: each major surface change consumes
  exactly one release window. P9.7 path rename + 308 install =
  v2.0 window event. P9.9 v1 subsystem + v1 router deletion + 410
  shim install = v2.0 window event. v2.1.0 = 308 + 410 retirement
  window. Bundling P9.7 + P9.9 + 308 retirement into a single
  v2.0 cycle would violate the spirit of Q-B (one major change
  per window) and make the v2.0.x rollback surface harder to
  reason about.
* **Sentinel discipline**: through v2.0.x the FastAPI registry
  carries **83 mint + 9 spec + 9 shim** routes; the 9 shim routes
  are ``include_in_schema=False`` so they live in
  ``_openapi_snapshot.json`` only as the snapshot's literal
  record of what FastAPI registered, not as schema-advertised
  endpoints. At v2.1.0 the snapshot drops the 9 shim slots;
  sentinel #7 fails (good -- intended forcing function) until
  the snapshot is regenerated.

## Alternatives rejected

* **Option (a) -- retire in P9.9-epsilon (same release window as
  v1 deletion)**: REJECTED. Compresses P9.7 path rename + P9.9
  v1 subsystem deletion + 308 shim retirement into a single v2.0
  cycle, violating the spirit of Q-B (which explicitly defers
  ``api/routes/orgs.py`` hard-delete to v2.1.0 for the same
  reason). Also removes the safety net for any straggler caller
  -- admin scripts, webhooks, manual curl users, IM channel
  adapters not on the P9.8 frontend swap path -- one full release
  earlier than the 1-release-window contract promises.
* **Option (c) -- hybrid 410 Gone / 404**: REJECTED. Returning
  410 from ``_orgs_v2_legacy_redirects.py`` instead of 308
  inverts the shim's semantic (308 says ``moved permanently,
  retry here``; 410 says ``gone, do not retry``). 308 already
  encodes the migration signal correctly; layering a second
  sunset semantic on top confuses callers (which signal wins?)
  and contradicts the P9.7a-2a commit message rationale (308
  deliberately chosen over 301/302 to preserve method + body).
  404 at v2.1.0 is the natural endpoint -- a 410 wrapper adds
  code without adding information.

## Implementation notes

The 9 shim routes (paths under prefix ``/api/v2/orgs`` in
``_orgs_v2_legacy_redirects.py``): ``GET /templates``,
``GET /templates/{template_id}``,
``POST /templates/{template_id}/instantiate``, ``GET ""``
(list orgs), ``POST ""`` (create org), ``GET /{org_id}``,
``PATCH /{org_id}``, ``DELETE /{org_id}``,
``GET /{org_id}/stream``. All 9 redirect to the matching
``/api/v2/orgs-spec`` twin via RFC 7538 308 Permanent
Redirect, query string round-tripped verbatim,
``include_in_schema=False`` so they do not pollute the public
OpenAPI surface.

Execution plan for the v2.1.0 task lives in the forthcoming
**P-RC-9-P9.9-CHARTER section 8** (separate task; this ADR only
ratifies the *governance* decision). Sentinel updates required
at v2.1.0:

* **Sentinel #7** (``test_rest_contract_sentinel.py`` +
  ``_openapi_snapshot.json``): regenerate snapshot to drop 9
  shim entries; expected count ``83 mint + 9 spec``.
* **Sentinel #8** (``test_frontend_stale_paths_sentinel.py``):
  audit-only sweep; whitelist update needed only if Group C
  debug-only paths (``/reset``, ``/heartbeat/trigger``,
  ``/standup/trigger``) are impacted by independent v2.1.0
  work.

This ADR introduces no source / test / apps changes; it is
purely a governance ratification.

## Refs

* **Q-B (P-RC-9)** -- ``docs/revamp/Q_DECISIONS.md`` row at line
  53 (1-release shim window; hard-delete moves to v2.1.0). This
  ADR extends the same single-window discipline from the 410
  shim to the 308 shim symmetrically.
* **G-RC-9.7 audit** -- ``docs/revamp/gates/G-RC-9.7.md`` section
  13 closing matrix (``ADR-0015 NOT filed``; flagged as
  candidate trigger).
* **G-RC-9.8 audit** -- ``docs/revamp/gates/G-RC-9.8.md`` section
  8 (defer 308 retirement to v2.1.0) + section 13 (P9.9 charter
  must file ADR-0015).
* **P9.7a-2a commit** ``31332276`` -- physical landing of the
  9-route 308 shim and the rationale for 308 over 301/302.
* **ADR-0011** (subsystem decomposition) -- shim is
  intentionally NOT a Protocol (thin APIRouter).
* **ADR-0012** (no shim under v1) -- symmetric statement for
  the v2-side shim: ADR-0012 governs v1 deletion while
  ADR-0015 governs v2-side compat-layer retirement.
* **ADR-0014** (LOC budget revision) -- mirrors its 6-section
  layout (metadata + Context + Decision + Consequences +
  Alternatives + Refs).
