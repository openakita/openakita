# Q_DECISIONS.md -- single source of truth for operator decision points

This ledger records every Q-* user decision point raised across
the OpenAkita backend revamp, with the answer the operator
gave, the date, and the gate review where the answer was
captured. Future agents who need to know "who decided what
when" should consult this file first instead of grepping plan
revisions or chat history.

The ledger is **append-only**: once a row lands it must not be
silently rewritten. If a decision is overturned, add a NEW row
that supersedes the prior one, with a "supersedes" pointer.

## Original full-backend revamp plan (``openakita_full_backend_revamp_e6d8610d.plan.md``)

The original plan was self-described as having no operator-
chosen branches: the author proposed a single phased order
(Phase 0 ADRs -> Phase 1 Foundation -> Phase 2 Agent -> ... ->
Phase 8 Legacy removal). The G-RC-8 gate review explicitly
notes "no operator answer; defaulted to phased order" for the
single open question the plan flagged (Phase 5 timing of
template registry vs Phase 4 nodes).

| Q-ref | question | answer | answered on | recorded at |
|---|---|---|---|---|
| (none) | (no formal Q-* points in original plan; phased order assumed throughout) | -- | -- | -- |

## Continuation plan (``openakita_revamp_continuation_plan_d6192647.plan.md``) section 12

The continuation plan section 12 asked three operator questions
(Q-A timeline / Q-B Phase-5 boundary / Q-C parity scope). All
three were defaulted to the plan author's recommendation
because the operator did not respond in the planning window.
The defaults are recorded in the G-RC-8 review notes.

| Q-ref | question | accepted answer | answered on | recorded at |
|---|---|---|---|---|
| Q-A (cont) | timeline aggressive (2w) vs normal (4w) vs conservative (6w) | (b) normal 4w (defaulted) | 2026-05-19 (no operator answer; defaulted) | ``docs/revamp/gates/G-RC-8.md`` |
| Q-B (cont) | Phase-5 templates land before or after Phase-4 nodes | (a) after nodes (defaulted) | 2026-05-19 (no operator answer; defaulted) | ``docs/revamp/gates/G-RC-8.md`` |
| Q-C (cont) | parity-suite scope (every subsystem vs reasoning/agent only) | (b) reasoning/agent only (defaulted) | 2026-05-19 (no operator answer; defaulted) | ``docs/revamp/gates/G-RC-8.md`` |

## P-RC-9 plan (``docs/revamp/P-RC-9-PLAN.md``) section 7

Three open decisions raised at the G-RC-9.0 mini-gate review.
All three were **explicitly ACCEPTED** by the operator in
conversation on 2026-05-19 -- this is the first time in the
revamp history that operator answers landed in the same review
cycle as the questions, rather than defaulting.

| Q-ref | question | accepted answer | answered on | recorded at |
|---|---|---|---|---|
| Q-A (P-RC-9) | runtime/orgs/ keep prefix vs rename to orgs_v2 vs reclaim orgs | **(a) keep** -- ``runtime/orgs/`` stays; wholesale ``runtime/`` flattening deferred to P-RC-10 | 2026-05-19 | this commit + ``docs/revamp/P-RC-9-PLAN.md`` section 7 |
| Q-B (P-RC-9) | v1 REST endpoint deletion: hard-delete vs 1-release 410-shim vs full passthrough | **(b) 1-release HTTP 410 Gone shim** -- hard-delete in v2.1.0 | 2026-05-19 | this commit + ``docs/revamp/P-RC-9-PLAN.md`` section 7 |
| Q-C (P-RC-9) | timeline: 2w aggressive vs 4w normal vs 6w conservative | **(b) 4 weeks normal** -- one engineer full-time, 5-10 commits/day, mini-gates per phase | 2026-05-19 | this commit + ``docs/revamp/P-RC-9-PLAN.md`` section 7 |

### Q-A rationale recap

Renaming the v2 path would cost commits + caller churn for
zero behavioural benefit. ``runtime/`` consistency (every other
v2 surface lives under it: supervisor, templates, state_graph,
nodes) wins. The deeper "should runtime/ exist at all after v1
dies" question is real -- but it is the topic of P-RC-10 (v2.1.0
hygiene), not P-RC-9 (v2.0.0 behaviour-preservation).

### Q-B rationale recap

410 Gone with a body pointing at the v2 equivalent gives
operators one release to migrate clients without making the v2
codebase carry actual passthrough wiring. Mirrors the P-RC-7
shim cadence (``core/agent.py`` shim removed at v2.0.0-rc2).
Hard-delete moves to v2.1.0.


**Governance ratification (2026-05-20)**: ADR-0015 (``docs/adr/0015-308-shim-retirement-governance.md``) extends Q-B's 1-release-window discipline symmetrically to the v2-side P9.7 308 shim (``_orgs_v2_legacy_redirects.py``, 9 routes; landed P9.7a-2a ``31332276``). G-RC-9.7 + G-RC-9.8 audits both recommended option (b); ADR-0015 ratifies. Both 308 (v2-side) and 410 (v1-side) shims retire at v2.1.0; P9.9 is a documented NO-OP for the 308 shim per the same single-window contract.

### Q-C rationale recap

4 weeks matches the charter projection and P-RC-4..P-RC-7
historical cadence. The mini-gate-per-phase pattern keeps
scope creep bounded. P9.6 (OrgRuntime) and P9.7 (80-endpoint
REST mint) carry the natural slack; if either slips the
calendar absorbs to 5-6 weeks without triggering a re-plan.

## Future decisions

When P-RC-10 opens, its plan section 7 will raise its own
Q-* points (likely: Category C resolutions for ``state_graph``,
``nodes``, ``templates``, ``backends``). Those will append to
this file under a new "P-RC-10 plan" section.

## How to use this ledger

* Reading: scan the "accepted answer" column to learn what the
  operator chose. Cite this file (e.g. ``Q-A (P-RC-9) =
  ACCEPTED (a) keep, 2026-05-19, Q_DECISIONS.md``) instead of
  re-reading plan revisions.
* Writing: when a Q-* is answered, append the row in the SAME
  commit that propagates the answer into the plan / charter.
  Do NOT split "plan update" and "ledger row" into separate
  commits -- they belong together.
* Overturning: if a decision is later reversed, add a fresh
  row with ``supersedes:`` pointing at the older row by Q-ref
  + date. Never edit the older row in place.
