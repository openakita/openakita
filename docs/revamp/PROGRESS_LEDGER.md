# Revamp Progress Ledger

> Source of truth for every commit landed on `revamp/v2` during
> the post-RC continuation phases (P-RC-0 → P-RC-8). One row per
> commit, in commit order. Each row is appended *in the same
> commit that produced it* (or the next commit, when the producing
> commit is the row source itself, i.e. commit 4 of P-RC-0 which
> bootstraps this file).
>
> Rules of the ledger (per continuation plan §0.3):
> * append-only — once a row lands on `revamp/v2` it must not
>   be silently rewritten;
> * `LOC delta` and `tests delta` are signed integers,
>   positive = grew, negative = shrank, `0` = unchanged;
> * `ADR refs` lists the ADRs whose §s the commit implements.
>
> Pause points: every 5 commits, re-read `docs/revamp/STATUS.md`
> + this ledger + the relevant section of the original plan
> (`openakita_full_backend_revamp_e6d8610d.plan.md`) and the
> continuation plan
> (`openakita_revamp_continuation_plan_d6192647.plan.md`) before
> opening the next commit.

## P-RC-0 — Truth alignment & drift guardrails

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `b2abc4db` | P-RC-0 #1 | chore(config): align runtime_v2_enabled doc with its default=True | +22 | 0 | ADR-0010 |
| `15da567e` | P-RC-0 #2 | docs(revamp): write rollback.md (plan §7 mitigation, was missing) | +141 | 0 | ADR-0010 |
| `5f5e269e` | P-RC-0 #3 | chore(repo): gitignore local smoke-e2e artifacts; relocate to tests/artifacts/ | +9 | 0 | — |
| `30d5a287` | P-RC-0 #4 | tooling(revamp): add LOC invariant audit + PROGRESS_LEDGER scaffolding | +380 | +2 | — |
| `528db8d1` | P-RC-0 #5 | test(parity): kill facade-self-equivalence false positives | +278 / -18 | +6 | — |
| _this commit_ | P-RC-0 G | docs(revamp): G-RC-0 gate review (post-RC continuation phase 0 done) | +174 (gate note) + STATUS row + ledger fill-in | 0 | — |

## P-RC-1 — IM truly lands on the v2 Supervisor

_Not started._ Awaiting user signoff on G-RC-0.

## P-RC-2 — Frontend v2 wiring

_Not started._

## P-RC-3 — Multi-process-safe v2 persistence

_Not started._

## P-RC-4 — Phase 2 real slim-down: brain / tools / context

_Not started._

## P-RC-5 — Phase 2 real slim-down: reasoning_engine

_Not started._

## P-RC-6 — Phase 2 real slim-down: agent.py

_Not started._

## P-RC-7 — Caller migration + legacy bulk delete

_Not started._

## P-RC-8 — Endgame (renames, docs, acceptance, release)

_Not started._
