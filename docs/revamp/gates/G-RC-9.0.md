# G-RC-9.0 mini-gate -- P9.0 baseline (branch + recon + plan + ADRs + parity scaffold)

> **Status:** auto-signoff for the **P9.0 sub-gate only**. NOT
> the full G-RC-9 gate -- that lives at ``G-RC-9.md`` and signs
> off the entire P-RC-9 phase after P9.10 closes.
>
> Awaiting user review of ``docs/revamp/P-RC-9-PLAN.md`` (answers
> to Q-A / Q-B / Q-C in plan section 7) before P9.1
> (OrgBlackboard) is allowed to open.

## Scope

P9.0 lays the foundation for the P-RC-9 ``orgs/`` integral
migration. Per the charter (``docs/revamp/P-RC-9-CHARTER.md``)
and the original task brief, P9.0 ships paperwork + scaffolding
only -- **no production source under src/openakita/orgs/ or
src/openakita/runtime/orgs/ is touched**. Subsystem
implementation begins at P9.1 only after the user has reviewed
the plan and approved the launch.

## Commits

| Phase | Hash | Subject |
|---|---|---|
| P9.0a | ``f1833fe5`` | chore(p-rc-9): initialise revamp/v3-orgs branch + bump ledger to P-RC-9 |
| P9.0b | ``e3308eaf`` | docs(p-rc-9): write recon report part 1 (P-RC-9-RECON.md 0/1a/1b) |
| P9.0b2 | ``75aebde2`` | docs(p-rc-9): append recon report part 2 (sections 1c/1d/1e/1f + appendices) |
| P9.0c | ``205973ce`` | docs(p-rc-9): write execution plan part 1 (P-RC-9-PLAN.md sections 0/1/2/3) |
| P9.0d | ``e78ef3dd`` | docs(p-rc-9): write execution plan part 2 (P-RC-9-PLAN.md sections 4/5) |
| P9.0e | ``f7425326`` | docs(p-rc-9): write execution plan part 3 (P-RC-9-PLAN.md sections 6/7/8) |
| P9.0f | ``1d5a8938`` | docs(adr): add ADR-0011 (org subsystem decomposition) |
| P9.0g | ``46e8c884`` | docs(adr): add ADR-0012 (orgs/ deletion strategy) |
| P9.0h | ``2d60189c`` | docs(adr): add ADR-0013 (wall-clock SLA tests for cancel + checkpoint) |
| P9.0i | ``066524d4`` | feat(tests): scaffold tests/parity/orgs/ harness skeleton |
| P9.0z | _this commit_ | docs(revamp): write G-RC-9.0 mini-gate (P9.0 baseline ready) |

Total: 11 commits (P9.0a/b/b2/c/d/e/f/g/h/i/z), every commit
well under the 380 commit_guard threshold (largest was P9.0c at
363 hand-written LOC; smallest P9.0a at 64). Branch
``revamp/v3-orgs`` is at HEAD = this commit; ``revamp/v2`` and
the ``v2.0.0-rc1`` / ``v2.0.0-rc2`` tags are unchanged.

## Test counts (before vs after P9.0)

| target | revamp/v2 HEAD (594d5cb1) | revamp/v3-orgs HEAD (after P9.0z) | delta |
|---|---|---|---|
| ``tests/runtime`` | (unchanged) | (unchanged) | 0 |
| ``tests/agent`` | (unchanged) | (unchanged) | 0 |
| ``tests/api`` | (unchanged) | (unchanged) | 0 |
| ``tests/parity`` | 5 xfailed | **11 xfailed** | +6 xfailed (P9.0i skeleton) |
| ``tests/unit/test_plugins`` | (unchanged) | (unchanged) | 0 |
| **combined main gate** | **1123 passed, 1 skipped, 5 xfailed** | **1123 passed, 1 skipped, 11 xfailed** | +6 xfailed |
| ``tests/integration`` trio (v2 IM canary + cancel + entrypoints) | 8 passed | 8 passed | 0 |

Pass count: **0 regression** (1123 passed unchanged). Xfail count
rises by exactly 6 -- one strict xfail per charter subsystem in
``tests/parity/orgs/`` -- which matches the P9.0 gate criterion
"xfail count rises, never falls". Strict=True is set on every
placeholder so a future commit that accidentally introduces a
real pass on a placeholder fails loudly.

## LOC audit

``scripts/revamp_loc_audit.py`` -> ``exit 0``. The 15 tracked
files remain within budget:

* No legacy ``orgs/*`` or ``core/*`` file grew.
* The 5 ``agent/*`` files stayed within the +50 growth budget
  (none touched).
* P-RC-9 will extend ``TRACKED_FILES`` in P9.5/P9.6 when the
  legacy orgs/ files get moved under ``runtime/orgs/`` (rename
  pattern); for P9.0 the audit baseline is unchanged from
  ``v2.0.0-rc2``.

## Ruff

``ruff check src/openakita tests/parity/orgs`` -> clean. The
P9.0i parity skeleton passes ruff with no exceptions (one
``from __future__ import annotations`` per file, no unused
imports, no line-length violations).

## ADR coverage

P-RC-9 introduces three ADRs, all Status: Proposed (will flip to
Accepted at G-RC-9 / P9.10 after implementation):

* ADR-0011 (subsystem decomposition) -- justifies 6 v2
  subsystems instead of the v1 monolith; rejects 1-class
  composition, 3-subsystem grouping, and 10-subsystem
  micro-split alternatives.
* ADR-0012 (orgs/ deletion strategy) -- justifies direct delete
  for source files (no rename-shim interim because nothing
  imports the v1 paths after P9.8) and 1-release 410-Gone shim
  for the v1 REST endpoints.
* ADR-0013 (wall-clock SLA tests for cancel + checkpoint) --
  pins the 2/3/2-second budgets that close ACCEPTANCE.md
  criterion 2 caveat at P9.4 implementation.

## What P9.0 did NOT do (out of scope by design)

* No file under ``src/openakita/orgs/`` was touched.
* No file under ``src/openakita/runtime/orgs/`` was touched
  beyond the 3 storage-only files inherited from P-RC-3 (which
  remain at ``__init__.py`` 20 + ``store.py`` 204 +
  ``sqlite_store.py`` 188 = 412 LOC).
* No REST endpoint under ``api/routes/`` was added or modified.
* No caller under ``src/openakita/`` was migrated.
* No legacy test under ``tests/orgs/`` was deleted.
* The ``data/orgs/`` filesystem layout is untouched.

This is the **planning + scaffolding** phase. Subsystem
implementation starts at P9.1 (OrgBlackboard).

## Outstanding user decisions (gate the P9.1 launch)

Plan section 7 lists three decisions. Defaults below are the
recommendation; the user may override any of them.

* **Q-A** (path naming): default ``runtime/orgs/`` (no rename).
* **Q-B** (v1 REST shim window): default 1-release HTTP 410
  Gone shim, hard-delete in v2.1.0.
* **Q-C** (timeline): default 4 weeks normal, one engineer.

The next action is **operator review** of
``docs/revamp/P-RC-9-PLAN.md`` and ack of these three answers
(or revisions). The G-RC-9.0 status flips from "auto-signoff /
awaiting review" to "signed" once the user acks; only then does
P9.1 open.

## Discipline reminders carried forward from P-RC-0..P-RC-8

These are not new in P-RC-9; they are the same guardrails that
shipped 1123 green tests on revamp/v2 and are restated so the
P9.1 executor does not re-discover them.

* **N3** (G-RC-1): every commit appends a ledger row in the
  SAME commit. ``git add docs/revamp/PROGRESS_LEDGER_P9.md``
  before ``git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>"``.
* **N4** (G-RC-1): per-commit diff strictly under 400 LOC; WARN
  at 380. Run ``scripts/revamp_commit_guard.py --staged`` before
  every commit.
* **N5** (G-RC-1): commit messages via Python tempfile
  (``Path("commit_msg.tmp").write_text(msg, encoding='utf-8')``
  then ``git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -F commit_msg.tmp``); never PowerShell
  ``Out-File -Encoding utf8`` (UTF-8 BOM corruption).
* **N12** (G-RC-5): commit_guard two thresholds -- 380 WARN /
  400 REJECT. Earlier mentions of a single 380 cap were
  imprecise.
* **T1** (G-RC-2): commit_guard is a manual step (no git hook
  installed) so a legitimate one-off giant commit can still be
  force-recorded with eyes open.
* **P-RC-9-specific**: every commit appends to
  ``docs/revamp/PROGRESS_LEDGER_P9.md`` (separate from the main
  ``PROGRESS_LEDGER.md`` which is frozen at P-RC-8 close).
* **P-RC-9-specific**: every 5 commits, pause and re-read
  ``docs/revamp/P-RC-9-PLAN.md`` + this ledger + the relevant
  recon section + the active ADR. Write a 1-line "still-aligned
  check at commit N" note in the ledger.

## What G-RC-9.1 (the next mini-gate) needs to assert

After P9.1 (OrgBlackboard) closes, the G-RC-9.1 mini-gate
should report:

* ``runtime/orgs/blackboard.py`` exists with 8 public methods
  and < 400 LOC.
* ``tests/runtime/orgs/test_blackboard.py`` adds 12 cases.
* ``tests/parity/orgs/test_blackboard_parity.py`` has 8 real
  cases and 0 xfail (was 1 xfail in P9.0).
* tests/parity/orgs xfail count drops from 11 to 10 (was 11
  after P9.0).
* No regression in main pytest gate.
* LOC audit still exit 0.
* ruff clean over the new files.

The same template applies for G-RC-9.2..G-RC-9.6 with the
fixture counts and method counts adjusted per plan section 4.

---

**P9.0 done. STOP. Await user review of P-RC-9-PLAN.md and
answers to Q-A/Q-B/Q-C before opening P9.1.**
