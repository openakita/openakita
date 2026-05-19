# ADR-0012 -- ``orgs/`` deletion strategy

- **Status**: Proposed
- **Date**: 2026-05-19
- **Phase**: P-RC-9 P9.0g (will flip to Accepted at G-RC-9 / P9.10)
- **Decision owner**: project owner
- **Implementer**: AI agent on ``revamp/v3-orgs``

## Context

After P9.1-P9.8 land (six v2 subsystems + 80 v2 REST endpoints +
86 production caller migrations + 216 test caller migrations),
the legacy ``src/openakita/orgs/`` package is dead code. P9.9
must remove it. Two precedents from P-RC-4..P-RC-7 inform the
strategy:

* P-RC-4..P-RC-6 used a **rename-then-shim-then-delete** pattern
  for the ``core/`` giants: ``git mv core/agent.py
  core/_agent_legacy.py``, then ``core/agent.py`` became a
  ~30-LOC shim that re-exported the v2 path, then the shim was
  deleted at P-RC-7 P7.14 once every caller had been migrated.
* P-RC-7 P7.14 (``a21cdd4b``) was the actual mechanical
  ``git rm`` of those shim files (5 files, 169 LOC) -- it
  succeeded because the previous phases had proven the caller
  set was empty.

P-RC-9 must pick: copy the rename-shim-delete pattern for
``orgs/``, or direct-delete since the v2 surface lives at a
different path (``runtime/orgs/``) and shimming would only
matter if callers still imported ``openakita.orgs.X``.

## Decision

**Direct delete** for the source files at P9.9. No rename
(``git mv X _X_legacy``) interim step. **Deprecation shim for
v1 REST endpoints** (default Q-B answer: 1-release window of
HTTP 410 Gone responses, then hard-delete in v2.1.0).

### Source file deletion (P9.9)

* P9.8 finishes with ``git grep -nE 'from openakita\.orgs'
  -- src/openakita/`` returning 0 hits (every production caller
  has been migrated).
* P9.8 also finishes with the same grep against ``tests/``
  returning 0 hits outside ``tests/orgs/`` (cross-cutting
  integration / unit tests migrated; ``tests/orgs/`` deleted
  as a coherent block in P9.9).
* P9.9a (single commit): ``git rm -r src/openakita/orgs/`` --
  removes 26 files. Commit body lists every removed path.
* P9.9b (single commit): ``git rm -r tests/orgs/`` -- removes
  48 test files. Commit body explains which tests were
  pre-migrated to ``tests/runtime/orgs/`` or
  ``tests/parity/orgs/`` and which were dropped as
  implementation-detail-only.
* P9.9c (single commit): drop the four ``orgs/*`` rows from
  ``docs/revamp/LOC_BASELINE.json`` and the matching entries
  from ``scripts/revamp_loc_audit.py`` TRACKED_FILES /
  INFO_ONLY_FILES.
* P9.9d (single commit, optional): if any test/import was
  missed, the full pytest run after P9.9a/b/c catches it; a
  targeted rework commit fixes the residual.

### v1 REST endpoint deletion (P9.7 + P9.9)

P9.7 adds the 80 v2 REST endpoints with 1:1 contract parity
to v1. The v1 REST file (``src/openakita/api/routes/orgs.py``,
2 145 LOC, 89 endpoints) becomes the deprecation shim:

* Every v1 endpoint handler body is replaced with a small
  helper that returns ``HTTPException(410, detail={"gone": True,
  "moved_to": "/api/v2/orgs/..."})``. The handler signatures,
  paths, and route-decorator metadata are preserved so the
  OpenAPI spec keeps documenting the deprecation.
* The shim ships for v2.0.0-rc3 (the P9.10 release tag) and
  remains for the entire v2.0.x line.
* In v2.1.0 (out of P-RC-9 scope) the v1 file is deleted
  outright.

## Alternatives considered

**A1: Rename-shim-delete (the P-RC-4..P-RC-6 pattern).**
``git mv orgs/X.py orgs/_X_legacy.py`` then ship a shim
``orgs/X.py`` that re-exports ``runtime.orgs.X``. Rejected
because the v2 path is **different** (``runtime/orgs/`` vs
``orgs/``); the shim would have nothing to shim against.
Nothing under v2 calls ``openakita.orgs.X``, so the shim would
have no readers. It would only add files to delete later.

**A2: Hard-delete v1 REST endpoints at P9.9.** Skip the
deprecation shim. Rejected because external clients (frontend
builds older than the P9.7 default-port flip, third-party IM
bots wired directly to the REST API) would silently 404 on
upgrade. The 410-Gone shim costs one release of carry but
gives operators a measurable migration window.

**A3: Full proxy passthrough.** v1 endpoints internally
forward to the v2 handler in the same FastAPI process.
Rejected because it adds real runtime cost (one extra dict
re-pack per request) and the maintenance burden of keeping
the proxy code in sync with v2 handler signatures. The 410
shim is simpler and more honest.

## Consequences

### Positive

* P9.9 is mechanically simple: 3-4 commits, mostly negative
  diff.
* Operators get a clear 1-release deprecation window for v1
  REST clients (the 410 response body explicitly points at
  the v2 path).
* No shim cruft to clean up in v2.1.0 source code (only the
  REST file, which is one rm).
* The deletion commit body lists every removed file, so the
  audit trail is the commit log itself.

### Negative / Accepted Cost

* If P9.8 misses a caller, the P9.9 ``git rm`` produces an
  immediate ImportError on the full pytest run; rework is
  required (estimated +1-2 commits if it happens).
* Operators on v2.0.x with mid-release upgrades may see 410
  responses they did not expect; mitigated by the
  RELEASE_v2.md v2.0.0-rc3 changelog explicitly listing every
  deprecated path.

## Links

* Charter: ``docs/revamp/P-RC-9-CHARTER.md`` -- the
  "git rm -r src/openakita/orgs/" line this ADR realises.
* Plan: ``docs/revamp/P-RC-9-PLAN.md`` ?4 (P9.7 + P9.9) and
  ?7 (Q-B decision matrix).
* Precedent: P-RC-7 P7.14 commit ``a21cdd4b`` (5-shim
  ``git rm`` for core/agent.py et al).
* Sibling ADRs: ADR-0011 (subsystem decomposition),
  ADR-0013 (wall-clock SLA tests).
