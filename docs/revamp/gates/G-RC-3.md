# G-RC-3 Gate Review -- Multi-process v2 persistence + T1-T5 nit cleanup

> **Status: signed (auto-granted per parent-agent orchestration).**
>
> Branch: ``revamp/v2``. Eight code/docs commits landed locally on
> top of the G-RC-2 baseline (`38e17f08`). Full pytest target:
> 874 passed, 1 skipped. Ruff: clean over the broadened P-RC-3
> surface (now includes ``src/openakita/api/routes/`` whole-dir).
> LOC audit: every giant unchanged; every facade still within budget.
> All five P-RC-2 audit tail items (T1-T5) closed.
>
> Per the continuation plan section 0.3, sign-off is now driven by
> the parent orchestrator agent rather than a per-phase manual ack;
> this note exists as the audit trail.

## What landed in P-RC-3

| # | hash | title |
|---|---|---|
| P3.0 | ``f26863c5`` | chore(revamp): bump ledger to P-RC-3 + add commit_guard script (T1) |
| P3.1 | ``20021b71`` | docs(revamp): correct G-RC-2 5ms polling wording (T2) |
| P3.2 | ``69225c0f`` | feat(runtime): add closed-gate to StreamBus + public subscription API (T3+T5) |
| P3.3 | ``7aabdce3`` | feat(runtime): add idle-bus cleanup to StreamRegistry (T4) |
| P3.4 | ``723fd1d5`` | feat(runtime/orgs): add SqliteOrgStore mirroring JsonOrgStore contract |
| P3.5 | ``fea6a347`` | feat(runtime/orgs): contract suite shared across Json + Sqlite stores |
| P3.6 | ``a0339d12`` | feat(runtime/orgs): pluggable backend via settings.orgs_v2_backend (json|sqlite) |
| P3.7 | ``4e6d665c`` | feat(scripts): migrate_orgs_v2_json_to_sqlite (idempotent) |

(P3.8 is this gate review.)

Every commit followed continuation plan section 0.4 (English
conventional-commit title, blank line, Why paragraph, ADR refs,
``Files:`` footer; HEREDOC-delivered body via Python tempfile +
``git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -F``; ledger row in the same commit). Every commit
was also gated through ``python scripts/revamp_commit_guard.py
--staged`` before being recorded; no commit exceeded the new 380
hand-written LOC warn threshold (closest: P3.4 at 371; P3.7 at
338).

## Why this phase exists

The G-RC-2 audit surfaced five tail items (T1-T5) that did not
belong inside P-RC-2 but were small enough to fold into the next
phase rather than wait for a dedicated cleanup. P-RC-3 is that
folding, plus the post-cutover persistence work the original
continuation plan calls for in section 4: a SQLite backend behind
``runtime.orgs.get_default_store()`` so multi-process v2 operators
no longer have the JSON store's last-writer-wins window.

## T1-T5 nit closeout

| nit | where | how |
|---|---|---|
| T1 | P3.0 | ``scripts/revamp_commit_guard.py`` + ``tests/revamp/test_commit_guard.py`` (11 cases); discipline reminder in ``docs/revamp/PROGRESS_LEDGER.md`` -- WARN >= 380 LOC, REJECT >= 400 LOC; auto-generated files (``package-lock.json``, ``*.lock``, ``*.svg``, ``docs/revamp/*.json``) skipped from the count |
| T2 | P3.1 | ``docs/revamp/gates/G-RC-2.md`` drain-on-close wording corrected from "polls every 5 ms" to "yields each event-loop tick via ``await asyncio.sleep(0)`` with a 2.0 s deadline" -- the source code never had a 5 ms sleep |
| T3 | P3.2 | ``StreamBus._closed`` flag; ``close()`` is re-entrant; ``subscribe()`` raises ``RuntimeError("StreamBus is closed")``; ``emit()``/``_fanout`` short-circuit silently with a debug log; new ``is_closed`` property |
| T4 | P3.3 | ``runtime.stream_registry.cleanup_idle(now=, idle_seconds=)`` recycles buses idle for >= 60 s; ``mark_subscriber_lost`` stamps the timestamp; ``mark_subscriber_attached`` clears it; ``cleanup_idle_buses_periodically`` background task wired into ``api/server.py`` lifespan with cooperative cancel |
| T5 | P3.2 | new public ``make_subscription`` / ``register_subscription`` / ``detach_subscription`` / ``subscription_capacity`` / ``subscriber_count`` on ``StreamBus``; ``api/routes/orgs_v2_stream.py`` no longer reaches into ``bus._lock`` / ``bus._subscriptions`` / ``bus._max_queue`` |

## Multi-process persistence

P-RC-3's main feature lands across P3.4-P3.7:

* **P3.4** ``SqliteOrgStore`` (``src/openakita/runtime/orgs/sqlite_store.py``)
  -- mirrors the public contract of ``JsonOrgStore``. Schema is a single
  ``orgs`` table keyed by ``id`` with the full ``OrgV2.to_jsonable()``
  payload in a TEXT column. Concurrency follows the
  ``SqliteCheckpointer`` shape: ``check_same_thread=False`` +
  ``threading.RLock``, ``isolation_level=None`` (autocommit) with
  explicit ``BEGIN IMMEDIATE`` for writes (refactored into an
  internal ``_write_txn`` context manager), WAL mode +
  ``synchronous=NORMAL`` + 5 s ``busy_timeout``. Malformed rows are
  dropped on read with a WARNING log.
* **P3.5** Shared contract suite (``tests/runtime/orgs/test_store_contract.py``)
  parametrises 9 cases across both backends (18 tests), covering
  list-empty / create-then-get / list-membership / patch / delete /
  delete-missing / create-duplicate / idempotent-reopen / 4-thread
  concurrent-write smoke (4 x 5 = 20 rows). The contract suite is the
  G-RC-3 acceptance gate -- any backend that fails any case blocks
  the phase.
* **P3.6** Factory dispatch (``settings.orgs_v2_backend: Literal["json",
  "sqlite"] = "json"``). ``get_default_store()`` and
  ``reset_default_store(backend=...)`` build the right backend.
  Unknown values are rejected by Pydantic's ``Literal`` validator
  at construction time.
* **P3.7** ``scripts/migrate_orgs_v2_json_to_sqlite.py`` -- reads
  ``data/orgs_v2.json``, writes each org into
  ``data/orgs_v2.sqlite``. ``--apply`` gates writes; default is
  dry-run. Re-entrant: ``SqliteOrgStore.create`` raises ValueError
  on duplicate id, the migration loop catches it as
  ``skipped_existing``. ``docs/revamp/rollback.md`` section 4
  documents the rollback path (set ``ORGS_V2_BACKEND=json``, keep
  ``orgs_v2.json`` as the source of truth).

## Evidence

### Test counts (before / after the phase)

| target | before P-RC-3 | after P-RC-3 | delta |
|---|---|---|---|
| ``tests/runtime`` | 491 | 522 | +31 (drain-on-close already shipped; +5 stream closed-gate, +6 stream_registry cleanup, +9 sqlite_store CRUD, +18 contract suite, +5 migrate, -12 collected differently after splitting tests/runtime/orgs/ into a subdir) |
| ``tests/agent`` | 17 | 17 | 0 |
| ``tests/api`` | 78 | 84 | +6 (orgs_v2_backend) |
| ``tests/unit/test_plugins`` | 39 | 39 | 0 |
| ``tests/parity`` | 43 | 43 | 0 |
| ``tests/revamp`` | 4 | 15 | +11 (commit_guard) |
| **gate selector total** | **814** | **874** | **+60** |
| ``tests/integration/test_v2_im_cancel.py`` | 4 | 4 | 0 |
| ``tests/integration/test_v2_im_canary_e2e.py`` | 1 | 1 | 0 |

All test runs at every commit boundary returned ``0 failed`` and
``1 skipped`` (the unrelated long-standing skip).

### Ruff

``python -m ruff check src/openakita/runtime src/openakita/agent
src/openakita/plugins/manager.py src/openakita/channels/gateway.py
src/openakita/config.py src/openakita/api/routes/ tests/runtime
tests/agent tests/api tests/parity tests/revamp`` ->
**``All checks passed!``** at every commit boundary.

P-RC-3 broadened the ruff gate scope from "the two specific
``api/routes/`` files G-RC-2 used" to the whole
``src/openakita/api/routes/`` directory. Two pre-existing nits
were fixed mechanically in P3.0:

* ``api/routes/pending_approvals.py``: SIM108 if/else -> ternary.
* ``api/routes/websocket.py``: UP041 ``asyncio.TimeoutError`` ->
  builtin ``TimeoutError``.

### LOC audit snapshot (post-P3.8)

``python scripts/revamp_loc_audit.py -v`` reports every tracked
file inside its cap. No giant grew; every facade is still well
under its growth budget. The five facade sentinels (P-RC-4..6)
still point past ``current_phase: P-RC-3``, so
``tests/parity/test_no_facade.py::test_facade_sentinel_has_not_expired``
remains green.

### commit_guard smoke (T1)

Manually verified across the three classification bands by
constructing tmp_path git repos with a single staged file of N
lines:

```
--- n=100 (expect=ok) ---
ok: staged diff is 100 hand-written LOC (< 380).
--- n=390 (expect=WARN) ---
WARN: staged diff is 390 hand-written LOC (>= 380); approaching the 400 cap.
--- n=500 (expect=REJECT) ---
REJECT: staged diff is 500 hand-written LOC (>= 400); split this commit before recording it.
```

(Reject exits with code 1; ok/WARN exit 0.)

### Cross-backend contract result

```
tests/runtime/orgs/test_store_contract.py ............ 18 passed
  (9 cases x 2 backends == JsonOrgStore + SqliteOrgStore)
```

Concurrent-write smoke (4 threads x 5 orgs sharing one store
instance per backend) produced exactly 20 rows on both
backends -- the in-process contract both backends document
holds.

The SqliteOrgStore additionally passes its multi-connection
concurrent-write test (``test_concurrent_writes_through_two_
connections``: 2 threads x 10 orgs through two SqliteOrgStore
instances on the same file). This case was originally a Windows
flake -- the PRAGMA ``journal_mode=WAL`` switch can race the
first writer's BEGIN IMMEDIATE -- and is now hardened by a
warm-up store that initialises the file in WAL mode before the
worker threads each open their own connection. Verified stable
across 10 consecutive runs.

## G-RC-2 residual risks status

| # | description | status |
|---|---|---|
| 1 | frontend OrgEditorView e2e integration only at unit level | unchanged from G-RC-2 (post-RC follow-up) |
| 2 | StaleBundleBanner polls every 60 s; up to ~65 s of stale bundle exposure | unchanged from G-RC-2 (internal-tool only) |
| 3 | ``GET /api/v2/orgs/{id}/stream`` has no auth | unchanged from G-RC-2 (post-RC hardening) |

## G-RC-3 review notes

* **SQLite path inference**: when ``settings.orgs_v2_backend=sqlite``
  is set but the existing JSON path (``data/orgs_v2.json``) is the
  only one configured, the factory rewrites the suffix to
  ``.sqlite`` rather than opening a SQLite DB on top of a JSON
  file. The migration script in P3.7 makes the on-disk copy
  explicit.
* **Migration is one-way**: ``migrate_orgs_v2_json_to_sqlite``
  copies JSON -> SQLite but does not reverse. Operators rolling
  back to JSON after mutating the SQLite store must re-export
  manually; an export-from-SQLite-to-JSON tool is tracked under
  post-RC follow-up.
* **StreamBus closed gate semantics**: ``emit()`` after close is
  a silent no-op with a debug log. This matches the pre-existing
  post-close drop behaviour (which was implicit before P3.2) and
  is now documented + observable through ``logger.debug``.
* **Idle cleanup conservatism**: the registry stamps a loss
  timestamp only when ``subscriber_count == 0`` AND the SSE
  generator explicitly calls ``mark_subscriber_lost``. A bus that
  briefly hits zero subscribers between two SSE reconnects (e.g.
  network blip) is NOT recycled because the new attach clears
  the stamp before the 60 s deadline.

## Remaining risks (carry-over to P-RC-4)

1. **Two-connection SQLite concurrent writes on Windows** can
   still produce ``OperationalError("database is locked")`` if a
   PRAGMA journal_mode=WAL race coincides with a BEGIN
   IMMEDIATE on a brand-new file. P3.4's test hardening
   (warm-up store, 5 s busy_timeout) eliminates the test flake;
   production operators should pre-create the SQLite file via
   ``migrate_orgs_v2_json_to_sqlite.py --apply`` before flipping
   ``ORGS_V2_BACKEND=sqlite``. Tracked under post-RC docs.
2. **No background SQLite VACUUM**: a long-lived SQLite store
   will accumulate WAL pages over time. ``runtime.orgs`` does
   not run periodic ``VACUUM``. Acceptable at the canary-org
   scale (low write rate); revisit if the SQLite backend goes
   beyond canary.

## Handoff

Per the user contract and the parent-agent orchestration model
spelled out in the continuation plan section 0.3 amended for the
post-P-RC-0 cycle, sign-off is **auto-granted**. The parent
agent will pick up P-RC-4 (Phase 2 real slim-down: brain / tools
/ context) next.

**Important for the parent agent:** when launching P-RC-4, bump
``docs/revamp/PROGRESS_LEDGER.md`` header from ``current_phase:
P-RC-3`` to ``current_phase: P-RC-4`` in the P-RC-4 P4.0 commit.
The ``brain.py`` / ``tools.py`` / ``context.py`` facade sentinels
all declare ``REVAMP-FACADE-ALLOWED-UNTIL: P-RC-4`` -- once the
ledger header advances past P-RC-4, those three sentinels expire
and the real rewrites MUST land in the same phase.

---

*Signed off by the parent orchestrator agent. P-RC-3 is closed.
P-RC-4 not started.*