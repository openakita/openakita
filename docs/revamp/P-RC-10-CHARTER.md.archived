# P-RC-10 Charter -- ``runtime/`` hygiene flattening (deferred from P-RC-9)

**Status: NOT EXECUTED.** This document is the deferred-work
charter for the ``runtime/`` namespace flattening the P-RC-9
plan section 7 Q-A leaves at "keep ``runtime/orgs/`` for now".
It exists so the operator and the next plan author can pick
the work up after v2.0.0 ships without re-discovering the
scope.

P-RC-9 (currently in flight on ``revamp/v3-orgs``) is the
terminal phase of the v2.0.0 release train; nothing in this
charter is implemented before P-RC-9 closes. The
``src/openakita/runtime/`` package (~12 714 LOC across 57 .py
files in 10 subpackages plus 15 top-level files, measured on
``revamp/v3-orgs`` HEAD after P9.0z) is currently the v2 fork
scaffold ADR-0001 established to dodge the v1 ``orgs/`` name
collision. After P-RC-9 deletes ``src/openakita/orgs/``
wholesale (P9.9), the scaffold's primary justification
evaporates and the namespace becomes partial dead weight.

## 1. Why deferred (motivation)

The operator question that triggered this charter, asked
during the G-RC-9.0 review conversation on 2026-05-19:

> runtime ??????????????????????

(Translation: "Does the ``runtime/`` folder need to exist
forever? Won't it affect future analysis?")

The answer is **no, not forever -- but not yet**.

### 1.1 Why ``runtime/`` exists today

``runtime/`` was P-RC-0's fork-style-rewrite scaffold (see
ADR-0001 ``docs/adr/0001-fork-style-rewrite.md``). The
decision was: do not modify the legacy v1 packages in place;
mint a parallel v2 namespace under ``runtime/`` (engine-shaped
surfaces) and ``agent/`` (behavioural-shaped surfaces). This
let P-RC-1 through P-RC-8 ship v2 surfaces alongside the
still-live v1 ``orgs/`` and ``core/`` packages without name
collisions and without breaking any operator running the v1
surface in production.

### 1.2 Why the scaffold becomes dead weight after P-RC-9

Once P-RC-9 P9.9 lands::

    git ls-files src/openakita/orgs/ | wc -l   -> 0
    git ls-files src/openakita/core/  | wc -l  -> 0 (modulo
                                                  legacy
                                                  remnants
                                                  already
                                                  removed in
                                                  P-RC-7/8)

there is no longer a v1 surface to collide with. Several
subpackages under ``runtime/`` are then mis-located: they were
named after the engine pattern they implement
(``runtime/llm``, ``runtime/io``, ``runtime/context``,
``runtime/desktop``, ``runtime/guardrail``) but their only
consumers are inside ``agent/`` -- the behavioural surface.
Living under ``runtime/`` adds an indirection step every
reader has to mentally undo ("ah, ``runtime/io/`` is really
``agent`` IO helpers"). The operator's observation that this
"will affect future analysis" is exactly right: cognitive
overhead is a real maintenance cost.

### 1.3 Why P-RC-10 is the right time, not P-RC-9

P-RC-9 is a behaviour-preservation migration: it deletes v1
without changing any contract. Mixing in namespace flattening
during the same phase would:

* inflate per-commit LOC (every fold = additional import
  rewrites plus parity tests must run unchanged);
* make G-RC-9 review harder ("did this commit break orgs
  parity, or did the namespace move break something else?");
* delay v2.0.0 release by 1-2 weeks for pure namespace
  cosmetics.

P-RC-10 is sequenced **after** v2.0.0 ships precisely to keep
the two concerns separable.

## 2. Three categories (Category A / B / C)

Captured via ``.venv/Scripts/python.exe -c "from pathlib import
Path; ..."`` on ``revamp/v3-orgs`` HEAD after P9.0z. Total
runtime/ surface: **12 714 LOC across 57 .py files in 10
subpackages plus 15 top-level files**.

### 2.1 Category A -- permanently keep under ``runtime/``

These ARE the runtime stack (supervisor + stream + ledger +
checkpoint + messenger plus the persisted-state primitives),
or were re-confirmed by Q-A in P-RC-9-PLAN section 7.

| path | LOC | role |
|---|---:|---|
| ``runtime/__init__.py`` | 43 | package boundary |
| ``runtime/supervisor.py`` | 516 | outer Supervisor (ADR-0004) |
| ``runtime/stream.py`` | 475 | StreamBus + channels (ADR-0006) |
| ``runtime/cancel_token.py`` | 208 | cooperative cancel primitive |
| ``runtime/channel_routing.py`` | 384 | session+channel routing |
| ``runtime/session_bridge.py`` | 131 | v2 session adapter |
| ``runtime/im_stream_bridge.py`` | 155 | IM gateway bridge |
| ``runtime/ledger.py`` | 326 | TaskLedger (ADR-0004) |
| ``runtime/checkpoint.py`` | 313 | checkpoint contract (ADR-0005) |
| ``runtime/messenger.py`` | 401 | inter-node Messenger |
| ``runtime/models.py`` | 466 | OrgV2 + NodeV2 + EdgeV2 dataclasses |
| ``runtime/retry_policy.py`` | 322 | tool retry policy |
| ``runtime/event_store.py`` | 371 | event JSONL store |
| ``runtime/stall_detector.py`` | 245 | StallDetector (ADR-0004) |
| ``runtime/stream_registry.py`` | 214 | StreamBus registry |
| ``runtime/orgs/`` | 487 plus P9.1..P9.6 deltas | org subsystems (Q-A keeps) |

**Total Category A:** ~4 570 LOC top-level plus 487 LOC orgs/
baseline plus the P-RC-9 subsystem additions.

### 2.2 Category B -- fold to semantic home after v2.0.0 ships

These subpackages were named for the engine pattern they
implement but their only consumers are inside ``agent/``. They
should physically move under ``agent/`` so the import path
matches the dependency.

| current path | LOC | future home | rationale |
|---|---:|---|---|
| ``runtime/llm/`` | 583 (5 files) | ``agent/llm_helpers/`` | every public name is consumed only by ``agent/brain`` |
| ``runtime/io/`` | 223 (3 files) | ``agent/io_helpers/`` | truncate / overflow used only by ``agent/tools`` |
| ``runtime/context/`` | 294 (4 files) | ``agent/context_helpers/`` | helpers for ``agent/context`` |
| ``runtime/desktop/`` | 283 (2 files) | ``agent/desktop/`` | attachments helper for ``agent/core`` |
| ``runtime/guardrail/`` | 327 (3 files) | merge into ``agent/safety/`` | both packages enforce destructive-intent gating; one home |

**Total Category B:** ~1 710 LOC across 17 files.

### 2.3 Category C -- case-by-case decisions

These subpackages have ambiguous belonging. Each needs its
own P-RC-10 sub-decision and may legitimately end up in any
of: stay under ``runtime/``, promote to top-level, or fold
into ``agent/``.

| current path | LOC | candidate moves | open question |
|---|---:|---|---|
| ``runtime/state_graph/`` | 1 457 (10 files) | (i) stay; (ii) promote to top-level ``state_graph/`` | does anything outside ``runtime/`` import it? if yes, promote |
| ``runtime/nodes/`` | 2 128 (8 files) | (i) stay; (ii) fold into ``agent/reasoning_nodes/`` | nodes are LLM-driven; closer to behaviour than engine |
| ``runtime/templates/`` | 1 928 (8 files) | (i) stay; (ii) promote to top-level ``templates/`` | template registry is product-facing, not engine internal |
| ``runtime/backends/`` | 434 (3 files) | (i) stay; (ii) fold into ``llm/backends/`` | only consumer is ``llm/`` provider registry |

**Total Category C:** ~5 947 LOC across 29 files.

### 2.4 Three categories summed

| category | LOC | files | % of runtime/ |
|---|---:|---:|---:|
| A (keep) | ~5 057 | 16 plus ``orgs/`` tree | ~39% (will grow as ``orgs/`` lands) |
| B (fold to ``agent/``) | ~1 710 | 17 | ~13% |
| C (case-by-case) | ~5 947 | 29 | ~47% |
| **total** | **~12 714** | **57** | **100%** |

The exact Category C resolution is the operator's call at
P-RC-10 launch. P-RC-10 itself is structured so each sub-move
ships as its own mini-gate (G-RC-10.1, G-RC-10.2, ...) and
can be reversed without disturbing the others.

## 3. Estimated scope

* **Sub-moves:** 9-10 (one per Category B fold + one per
  Category C decision, plus a final ``__init__`` re-export
  cleanup).
* **Commits:** 15-25 (most moves are physical ``git mv`` plus
  a one-line import rewrite plus a re-export shim deletion; a
  few -- e.g. guardrail / agent.safety merge -- need a real
  deduplication pass).
* **Calendar:** 1-2 weeks for one engineer (gated by how
  many Category C sub-decisions need design review vs how
  many are obvious).
* **Pattern:** physical ``git mv`` + import redirect; **no
  shim needed** (no v1 around to confuse, every consumer is
  fully on the v2 surface after P-RC-9 closes).
* **Per sub-move:** its own mini-gate (G-RC-10.1, G-RC-10.2,
  ...) mirroring the P-RC-9 mini-gate pattern.
* **No new ADRs** unless a Category C decision contradicts an
  existing ADR; ADR-0014 (warning plugin authors about path
  moves) is the one expected addition (see section 5).

## 4. Hard trigger conditions (when may P-RC-10 start)

All five must be true before P-RC-10.1 opens:

1. **P-RC-9 fully shipped.** Every P9.x has landed including
   ``git rm -r src/openakita/orgs/`` at P9.9. ``git ls-files
   src/openakita/orgs/ | wc -l`` returns 0.
2. **``v2.0.0-rc3`` tag stable for >= 1 week.** Local or
   canary smoke test, ledger inspection, no critical-severity
   issue filed in the burn-in window.
3. **G-RC-9 independent audit PASS with no nits.** If the
   gate flags follow-on items (R-RC-9-A residuals), those
   land first as a P9.x-fix patch.
4. **ACCEPTANCE.md 5/5 Pass.** No "Pass-with-caveat" or
   "Partial" rows remain (P-RC-9 closes criteria 2 and 5 per
   plan section 8).
5. **(Recommended)** ``v2.0.0`` already merged to ``main``
   and tagged. Not strictly required (P-RC-10 is v2.1.0 prep)
   but keeps the release train coherent.

## 5. Release relationship

* P-RC-10 is a **v2.1.0 hygiene phase**, NOT a v2.0.0
  blocker.
* Justification: P-RC-10 is high mechanical disruption (every
  import path under ``runtime/{llm,io,context,desktop,
  guardrail}`` shifts) but low semantic risk (pure namespace
  move with no contract change). Tying it to v2.0.0 release
  would delay shipping by 1-2 weeks for pure cosmetics.
* **ADR-0014** (to be added in P-RC-10.0 paperwork) warns
  plugin authors that import paths under ``runtime/{llm,io,
  context,desktop,guardrail}`` will move in v2.1.0. The
  release notes for v2.0.0 should preview ADR-0014 so plugin
  ecosystem can plan ahead.

## 6. Operator concerns explicitly addressed

### "??????????" (won't future analysis be affected?)

Yes -- cognitive overhead is real. Mitigations layered:

* **Pre-P-RC-10 (v2.0.0 era):** add a single table to
  ``AGENTS.md`` mapping each ``runtime/`` subpackage to its
  semantic home plus source description. Add a 3-line
  docstring to each ``runtime/<subpkg>/__init__.py`` of the
  form ``<triple-double-quote>Source: <legacy path>. Service object: <what it
  owns>. Future home: <P-RC-10 target> (deferred).<triple-double-quote>``.
* **Post-P-RC-10:** the table collapses; only Category A
  remains under ``runtime/`` and is self-explanatory.

### "????????" (the old logic is already useless)

Exactly the right framing. P-RC-10 only makes sense AFTER v1
is dead. Before v1 deletion, the parallel ``runtime/``
namespace is doing real work (avoiding collision); only after
v1 deletion does it become surplus.

## 7. What this charter does NOT do

* Does NOT execute any moves. Zero ``git mv``. Zero source
  changes outside the optional AGENTS.md table mentioned in
  section 6.
* Does NOT modify any source code. No package boundary moves.
* Does NOT change any v2.0.0 release timeline. P-RC-9 ship +
  burn-in + tag remain on their current schedule.
* Does NOT pre-approve any specific Category C resolution.
  The operator picks at P-RC-10 launch.
* Does NOT add ADR-0014. That happens at P-RC-10.0 (the
  P-RC-10 paperwork phase, equivalent to P9.0).
* Awaits user explicit start signal after the five conditions
  in section 4 are all green.

## 8. Cross-references

* ``docs/revamp/P-RC-9-CHARTER.md`` -- the template this
  charter mirrors (deferred-work pattern from P-RC-8 P8.4).
* ``docs/revamp/P-RC-9-PLAN.md`` section 7 Q-A -- the
  decision to keep ``runtime/orgs/`` under ``runtime/`` that
  this charter formally records as the trigger for P-RC-10.
* ``docs/adr/0001-fork-style-rewrite.md`` -- the original
  justification for the ``runtime/`` namespace; P-RC-10
  formally retires part of that scaffold.
* ``docs/revamp/STATUS.md`` -- scoreboard footer (P-RC-10
  pointer added in this commit).
