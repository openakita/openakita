# Revamp Progress Ledger -- P-RC-10 (runtime/orgs -> orgs namespace flatten + 5 deferred nits + merge-to-main)

<!-- machine-readable phase marker; do NOT remove.
     Parsed by tests/revamp/_ledger.py + tests/parity/test_no_facade.py. -->
current_phase: P-RC-10

> **Sub-phase status (2026-05-21, P10.0a CHARTERED)**:
> P-RC-10 epic opened; this commit (the P10.0a charter
> ratification) is the first row below. P-RC-9 closed
> at G-RC-9 final eta-2 with 9 / 9 sentinels active +
> -35 493 LOC v1 retirement axis; 5 nits (M-2 / P9.7-B
> / epsilon-O1 / epsilon-O2 / GroupC) ride into P-RC-10
> per G-RC-9.9 section 3. Namespace flatten
> ``runtime/orgs/ -> orgs/`` is the primary axis (71
> files / 157 occurrences mechanically swept at P10.3).
> 308 shim retirement remains OUT-OF-SCOPE per ADR-0015
> option (b); deferred to v2.1.0 milestone.

> Source of truth for every commit landed on
> ``revamp/v3-orgs`` during the P-RC-10
> namespace-finalisation epic. One row per commit, in
> commit order. Each row is appended **in the same
> commit that produced it** (N3 from G-RC-1).
>
> This ledger is **separate** from
> ``docs/revamp/PROGRESS_LEDGER.md`` (frozen at
> P-RC-8) and ``docs/revamp/PROGRESS_LEDGER_P9.md``
> (closed at G-RC-9 eta-2). Keeping P-RC-10 in its own
> file preserves the per-epic clean diff lineage.
>
> Rules of the ledger (inherited from
> PROGRESS_LEDGER_P9):
> * append-only -- once a row lands it must not be
>   silently rewritten;
> * ``LOC delta`` and ``tests delta`` are signed
>   integers, positive = grew, negative = shrank,
>   ``0`` = unchanged;
> * ``ADR refs`` lists the ADRs whose sections the
>   commit implements (ADR-0011 / 0014 / 0015 are
>   P-RC-10-relevant; no new ADRs planned).

## P10.0 -- Charter ratification (paperwork)

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.0a | docs(revamp): expand P-RC-10 charter with sub-phases, nits, merge-to-main plan [P-RC-10-charter] | +PLACEHOLDER (overwrite ``P-RC-10-CHARTER.md`` ~+420 + archive prior 220 LOC as ``.archived`` + new ``PROGRESS_LEDGER_P10.md`` ~+45) | 0 | --- (planning; cites ADR-0011 / 0014 / 0015 as references; no new ADR) |
