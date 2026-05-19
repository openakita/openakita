# Revamp Progress Ledger -- P-RC-9 (orgs/ integral migration)

<!-- machine-readable phase marker; do NOT remove.
     Parsed by tests/revamp/_ledger.py + tests/parity/test_no_facade.py. -->
current_phase: P-RC-9

> Source of truth for every commit landed on ``revamp/v3-orgs``
> during the P-RC-9 ``src/openakita/orgs/`` integral migration.
> One row per commit, in commit order. Each row is appended *in
> the same commit that produced it* (N3 from G-RC-1).
>
> This ledger is **separate** from ``docs/revamp/PROGRESS_LEDGER.md``
> (which is frozen at P-RC-8 close). Keeping P-RC-9 in its own file
> stops the long P-RC-0..8 history from being mixed with the new
> 30-50 commits the charter projects, and lets future readers diff
> the two phases cleanly.
>
> Rules of the ledger (per continuation plan ?0.3, inherited):
> * append-only -- once a row lands on ``revamp/v3-orgs`` it must
>   not be silently rewritten;
> * ``LOC delta`` and ``tests delta`` are signed integers,
>   positive = grew, negative = shrank, ``0`` = unchanged;
> * ``ADR refs`` lists the ADRs whose sections the commit
>   implements (ADR-0011/0012/0013 are P-RC-9-specific).
>
> Pause points: every 5 commits, re-read
> ``docs/revamp/P-RC-9-PLAN.md`` + this ledger + the relevant
> phase section before opening the next commit.

## P9.0 -- Baseline (branch + recon + plan + ADRs + parity scaffold)

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``f1833fe5`` | P-RC-9 P9.0a | chore(p-rc-9): initialise revamp/v3-orgs branch + bump ledger to P-RC-9 | +PLACEHOLDER (new PROGRESS_LEDGER_P9.md + STATUS.md pointer) | 0 | --- (process; charter only) |
| ``e3308eaf`` | P-RC-9 P9.0b | docs(p-rc-9): write recon report part 1 (P-RC-9-RECON.md sections 0/1a/1b) | +246 (new file) | 0 | --- (read-only analysis) |
| ``75aebde2`` | P-RC-9 P9.0b2 | docs(p-rc-9): append recon report part 2 (sections 1c/1d/1e/1f + appendices) | +170 (append) | 0 | --- (read-only analysis) |
| ``205973ce`` | P-RC-9 P9.0c | docs(p-rc-9): write execution plan part 1 (P-RC-9-PLAN.md sections 0/1/2/3) | +361 (new file) | 0 | --- (planning) |
| ``e78ef3dd`` | P-RC-9 P9.0d | docs(p-rc-9): write execution plan part 2 (P-RC-9-PLAN.md sections 4/5) | +325 (append) | 0 | --- (planning) |
| ``f7425326`` | P-RC-9 P9.0e | docs(p-rc-9): write execution plan part 3 (P-RC-9-PLAN.md sections 6/7/8) | +180 (append) | 0 | --- (planning; previews ADR-0011/0012/0013) |
| ``1d5a8938`` | P-RC-9 P9.0f | docs(adr): add ADR-0011 (org subsystem decomposition) | +118 (new ADR) | 0 | ADR-0011 |
| ``46e8c884`` | P-RC-9 P9.0g | docs(adr): add ADR-0012 (orgs/ deletion strategy) | +137 (new ADR) | 0 | ADR-0012 |
| ``2d60189c`` | P-RC-9 P9.0h | docs(adr): add ADR-0013 (wall-clock SLA tests for cancel + checkpoint) | +123 (new ADR) | 0 | ADR-0013 |
| ``066524d4`` | P-RC-9 P9.0i | feat(tests): scaffold tests/parity/orgs/ harness skeleton (6 xfail placeholders) | +~200 (new package: __init__ + conftest + README + 6 test files) | +6 xfailed | ADR-0011 (subsystem list anchors the 6 placeholders) |
| _this commit_ | P-RC-9 P9.0z | docs(revamp): write G-RC-9.0 mini-gate (P9.0 baseline ready) | +183 (new gate file) | 0 | --- (gate review; cites ADR-0011/0012/0013) |

## P-RC-10 charter + Q decisions (post-P9.0z paperwork, pre-P9.1)

> Two paperwork commits that close the G-RC-9.0 review's open
> items (deferred-work charter for ``runtime/`` flattening +
> Q-A/Q-B/Q-C decision lock-in) BEFORE P9.1 OrgBlackboard work
> opens. Both commits are pure docs; no source touched.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``ff8f695a`` | P-RC-10 charter | docs(revamp): write P-RC-10 charter for runtime/ hygiene flattening (v2.1.0 prep) | +309 (new ``P-RC-10-CHARTER.md`` 272 + STATUS.md 27 + ledger 11) | 0 | --- (planning; previews ADR-0014) |
| _this commit_ | P-RC-9 Q-lock | docs(revamp): lock in Q-A/Q-B/Q-C defaults + write Q_DECISIONS.md ledger | +~250 (new ``Q_DECISIONS.md`` + P-RC-9-PLAN.md section 7 ACCEPTED markers + ledger) | 0 | --- (paperwork; cites ADR-0011/0012/0013 indirectly via plan section 7) |

## P9.1 -- OrgBlackboard (charter subsystem #1)

> Implements ADR-0011 subsystem #1 (charter section 1).
> Replaces v1 ``openakita.orgs.blackboard.OrgBlackboard`` (344
> LOC, 19 methods) with a Protocol-typed, backend-pluggable v2
> surface under ``runtime/orgs/`` while preserving v1''s public
> sync API verbatim (parity gate per P-RC-9-PLAN section 0.2).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``040256b2`` | P-RC-9 P9.1a0 | feat(runtime/orgs): add v2 memory models (MemoryScope/Type/OrgMemoryEntry) | +162 (memory_models.py NEW 127 + __init__.py +17/-10 + ledger) | 0 | ADR-0011 (subsystem decomposition; this is the shared model layer) |
| ``bcf43580`` | P-RC-9 P9.1a | feat(runtime/orgs): scaffold BlackboardProtocol + minimal v2 implementation | +315 (blackboard.py NEW 288 + __init__.py +20/-5 + ledger +3) | 0 (smoke import only) | ADR-0011 (Protocol-typed subsystem); ADR-0012 (no shim under v1) |
| ``57977dd0`` | P-RC-9 P9.1b | feat(runtime/orgs): complete v2 OrgBlackboard with concurrency + schema validation (JsonFile half) | +202 (blackboard.py +198/-4 + ledger +3) | 0 | ADR-0011; ADR-0013 |
| ``d1c8f235`` | P-RC-9 P9.1b2 | feat(runtime/orgs): add SqliteBlackboardBackend + get_default_blackboard_backend factory | +237 (blackboard.py +230 + __init__.py +4 + ledger +3) | 0 (sqlite smoke run during commit prep) | ADR-0011; ADR-0012 |
| ``7f3445e3`` | P-RC-9 P9.1c | test(parity/orgs): activate 8 blackboard parity fixtures (xfail -> pass) | +229 (test_blackboard_parity.py REPLACE: 18-line xfail placeholder -> 228-line fixture suite + ledger +3) | +8 / -1 xfail | ADR-0011; ADR-0013 |
| _this commit_ | P-RC-9 P9.1d | test(runtime/orgs): add 12 blackboard contract tests covering both backends | +348 (test_blackboard_contract.py NEW 345 + ledger +3) | +24 (12 cases x 2 backends; json + sqlite parametrised) | ADR-0011 (cross-backend observational equivalence); ADR-0012 (sqlite backend smoke-tested via every test) |
