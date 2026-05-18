# G-RC-0 Gate Review — Truth alignment & drift guardrails

> **Status: written, awaiting user signoff before P-RC-1 begins.**
>
> Branch: `revamp/v2`. Five commits landed locally (no push).
> Full pytest target: 763 passed, 1 skipped. Ruff: clean over the
> agreed v2 surface. LOC audit: all 14 tracked files within budget.
>
> Reviewer: please flip this status line to **signed** when you
> are happy with the evidence below; only then should P-RC-1 start.

## What landed in P-RC-0

| # | hash | title |
|---|---|---|
| 1 | `b2abc4db` | chore(config): align runtime_v2_enabled doc with its default=True |
| 2 | `15da567e` | docs(revamp): write rollback.md (plan §7 mitigation, was missing) |
| 3 | `5f5e269e` | chore(repo): gitignore local smoke-e2e artifacts; relocate to tests/artifacts/ |
| 4 | `30d5a287` | tooling(revamp): add LOC invariant audit + PROGRESS_LEDGER scaffolding |
| 5 | `528db8d1` | test(parity): kill facade-self-equivalence false positives |

Each commit follows continuation plan §0.4 (English conventional
commit title, blank line, Why paragraph, ADR/plan refs, `Files:`
footer; ≤ 400 LOC changed; HEREDOC-delivered body via `-F`).

## Why this phase exists

Before any further code rewrite (P-RC-1 onward), the post-RC plan
demanded three drift guardrails be **executable**, not just
documented, because the original plan audit
(`docs/revamp/PLAN_AUDIT.md`) showed the codebase had silently
drifted from the plan in four ways the test suite could not catch:

1. `runtime_v2_enabled`\'s comment said "default closed"
   while the field defaulted to `True` — the single biggest
   source of confusion for any reader landing in `config.py`.
2. The plan promised a Phase 7 rollback SOP; no document existed.
3. `core/*` and `orgs/*` giants had grown line-wise in some
   pre-rewrite commits because no machine rule rejected growth.
4. The 30-case parity harness reported 30/30 even when the v2
   path was a thin re-export of the v1 path (facade-self-
   equivalence false positive). G2 was effectively claimed
   against a tautology.

## Evidence

### Test counts (before / after the phase)

| target | before P-RC-0 | after P-RC-0 | delta |
|---|---|---|---|
| `tests/runtime` | 472 | 472 | 0 |
| `tests/agent` | 17 | 17 | 0 |
| `tests/api` | 65 | 65 | 0 |
| `tests/unit/test_plugins` | 39 | 39 | 0 |
| `tests/parity` | 32 | 38 | +6 |
| `tests/revamp` (new) | 0 | 2 | +2 |
| **total (selected)** | **755** | **763** | **+8** |

(`755 -> 763` measured via the same selectors the continuation
plan §0.4 uses; the global suite count differs by the unrelated
e2e and smoke files that are not part of the gate target.)

Plus 1 unrelated skipped test (consistent across before/after).
0 failures. 0 unexpected xfails.

### Ruff

`python -m ruff check src/openakita/runtime src/openakita/agent
src/openakita/plugins/manager.py src/openakita/channels/gateway.py
tests/runtime tests/agent tests/api tests/parity tests/revamp` →
**`All checks passed!`** at every commit boundary.

### LOC audit snapshot

`python scripts/revamp_loc_audit.py -v` at the end of P-RC-0:

`
file                                      current  baseline    cap  slack  status
---------------------------------------------------------------------------------
src/openakita/core/agent.py                  9602      9602   9602      0  ok
src/openakita/core/reasoning_engine.py       8725      8725   8725      0  ok
src/openakita/core/brain.py                  2015      2015   2015      0  ok
src/openakita/core/tool_executor.py          1818      1818   1818      0  ok
src/openakita/core/context_manager.py        1799      1799   1799      0  ok
src/openakita/orgs/runtime.py                6355      6355   6355      0  ok
src/openakita/orgs/tool_handler.py           3474      3474   3474      0  ok
src/openakita/orgs/templates.py              1266      1266   1266      0  ok
src/openakita/orgs/messenger.py               651       651    651      0  ok
src/openakita/agent/core.py                    68        58    108     40  ok
src/openakita/agent/reasoning.py               61        51    101     40  ok
src/openakita/agent/brain.py                   88        78    128     40  ok
src/openakita/agent/tools.py                   66        56    106     40  ok
src/openakita/agent/context.py                 57        47     97     40  ok
`

Note: the plan §0.1 listed `brain.py` baseline as `1914`; the
real on-disk count at this commit is `2015`. The audit uses the
on-disk measurement as the source of truth; the plan number was a
rounded approximation. `core/*` and `orgs/*` rows have
`cap == baseline`: any future commit that inflates them by even
one line trips the audit and the `tests/revamp/test_loc_invariants
.py` test fails on the next `pytest` run.

### Drift guardrails now in place

* `scripts/revamp_loc_audit.py` (CLI + importable module).
* `docs/revamp/LOC_BASELINE.json` (seeded measurements).
* `tests/revamp/test_loc_invariants.py` (pytest gate, 2 tests).
* `tests/parity/test_no_facade.py` (5 sentinel/SLOC structural
  checks + 1 phase-range pin).
* `tests/parity/harness.py` xfail guard against
  facade-self-equivalence (no v1_file == v2_file allowed
  silently).
* `docs/revamp/PROGRESS_LEDGER.md` (per-commit append-only
  ledger; rows for commits 1–5 of P-RC-0 already populated with
  hashes).
* `docs/revamp/rollback.md` (Phase 7 rollback SOP, including a
  4-curl verification checklist).

### Files touched (whole phase)

| file | change kind |
|---|---|
| `src/openakita/config.py` | rewrite Runtime v2 comment block (commit 1) |
| `docs/revamp/rollback.md` | new (commit 2) |
| `plugins/happyhorse-video/tests/smoke_e2e.py` | producer path (commit 3) |
| `plugins/happyhorse-video/tests/smoke_e2e_report.json` | filesystem-moved + gitignored (commit 3) |
| `.gitignore` | 3 new ignore rules (commit 3) |
| `scripts/revamp_loc_audit.py` | new (commit 4) |
| `docs/revamp/LOC_BASELINE.json` | new (commit 4) |
| `docs/revamp/PROGRESS_LEDGER.md` | new (commit 4) |
| `tests/revamp/__init__.py` + `test_loc_invariants.py` | new (commit 4) |
| `tests/parity/runners.py` | KIND_MODULES + dispatch stash (commit 5) |
| `tests/parity/harness.py` | file-equality xfail guard (commit 5) |
| `tests/parity/test_no_facade.py` | new (commit 5) |
| `src/openakita/agent/{core,reasoning,brain,tools,context}.py` | sentinel comment block (commit 5) |

## Residual risks

1. **Plan vs reality on `core/brain.py` baseline**: plan §0.1
   wrote `1914`, real count is `2015`. Documented in this gate
   note; the LOC baseline JSON is the source of truth. No action
   required, but reviewers should confirm the on-disk number is
   the right baseline before P-RC-4 starts.
2. **Sentinel coverage**: `test_no_facade.py` scans only the 5
   REWRITE targets the plan names (`core`, `reasoning`,
   `brain`, `tools`, `context`). Other `agent/*` modules
   (e.g. `permission`, `trusted_paths`, `user_profile`,
   `capabilities`) are MOVE commits; if any of those silently
   regresses to a re-export, the structural check will not
   catch it. The behavioural `v1_file != v2_file` guard in
   `assert_parity` does cover them via the harness, but only
   for kinds with a parity case.
3. **Windows/CRLF noise**: every commit shows
   `warning: LF will be replaced by CRLF` on the Windows dev
   host. Files on disk hold LF endings (we wrote them with
   `newline=\"\\n\"`); `core.autocrlf` is the local
   setting that surfaces the warning. This is purely cosmetic.
4. **The wider plan**: the post-RC plan still has 8 phases and
   ~80 atomic commits ahead. P-RC-0 is the cheapest of them by
   design — no behaviour change. Reviewers should weigh whether
   the timeline budget in continuation plan §11 still fits the
   schedule.

## Handoff

> User signoff required before P-RC-1.

Per continuation plan §0.3, the next phase (P-RC-1: IM truly
lands on the v2 Supervisor) is **explicitly gated** on user ack
of this gate review note. Do not start P-RC-1 until the reviewer
flips the "Status:" line at the top of this file to **signed**.

Suggested ack format in chat: "G-RC-0 signed; proceed to P-RC-1."
