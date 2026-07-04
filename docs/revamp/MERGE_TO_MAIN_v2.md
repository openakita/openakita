# MERGE_TO_MAIN_v2 -- ``revamp/v3-orgs`` -> ``main`` operator-gated charter

**Status**: DRAFT (P-RC-10 P10.7b). Awaits operator decision on
the §7 matrix and execution of the §3 pre-merge checklist before
``git merge`` runs.
**Branch**: ``revamp/v3-orgs``.
**Authored at**: 2026-05-22 (P10.7b).
**Lifts**: ``docs/revamp/P-RC-10-CHARTER.md`` §4 skeleton -> a
first-class standalone operator charter.
**Sister gate**: ``docs/revamp/gates/G-RC-10.md`` (PROVISIONAL;
verdict flips to PASS upon §7 row 4 sign-off).

## §0 Executive summary

``revamp/v3-orgs`` is the long-running epic branch carrying the
v2.0.0 backend revamp from P-RC-0 through P-RC-10. After ~18
months of phased work -- most recently P-RC-9 (v1 ``orgs/``
surface retirement) and P-RC-10 (``runtime/orgs/*`` flatten + 5
deferred nits + sentinel #9 hardening) -- the branch is in a
release-ready state with the in-epic narrow slice 100% green
and the broader pytest suite at 99.0% (60 carry-overs are
pre-existing legacy debt, NOT P-RC-10 regressions; see G-RC-10
§6). This charter ratifies the merge strategy, tag flow,
rollback procedure, and post-merge milestones so the operator
can land v2.0.0 in a single auditable session. **This document
does NOT execute the merge** -- the operator runs the §3
pre-merge checklist, signs G-RC-10 PROVISIONAL -> PASS, then
drives the §3 merge mechanics by hand.

## §1 Branch state at merge time

* **HEAD (``revamp/v3-orgs``)**: ``bdf635ff94c25e0518f1021aaf76f7264fc102c1`` (``bdf635ff``).
* **``main`` HEAD / merge base**: ``d456128baf969e43815522e98e1bcb1cb2c8101d`` (``d456128b`` -- ``chore(happyhorse-video): refresh bundled UI for s2v queue feedback``). ``git merge-base main revamp/v3-orgs`` equals ``main``'s tip, so ``main`` has not advanced since the fork point and the merge is a strict fast-forwardable line. We still pin ``--no-ff`` for epic-boundary preservation (see §3).
* **Commit ledger**: ``git rev-list --left-right --count main...revamp/v3-orgs`` -> **``0    378``** (zero commits on ``main`` not in ``revamp/v3-orgs``; **378 commits** on ``revamp/v3-orgs`` ahead of ``main``).
* **Diff stat**: ``git diff --stat main...revamp/v3-orgs | tail -1`` -> **``722 files changed, 94222 insertions(+), 43717 deletions(-)``**.
* **Existing local tags** (NOT pushed; verified at authoring): ``v2.0.0`` -> ``6905ecd4`` (P-RC-9 closure marker, mid-branch -- NOT branch tip), ``v2.0.0-rc1`` -> ``ed8f5c65``, ``v2.0.0-rc2`` -> ``3547ce21``. The dangling local ``v2.0.0`` is the operator lever for §2 option A vs B.

## §2 Tag strategy (decision required from operator)

| option | description | trade-off |
|---|---|---|
| **A (DEFAULT, recommended)** | After merge, **move** the local ``v2.0.0`` tag (currently at ``6905ecd4``) to the new ``--no-ff`` merge commit on ``main``. Single canonical ``v2.0.0`` hash; the merge commit IS the release point. | One tag, one release. Simplest CHANGELOG / release-notes anchor. The local-tag-move is safe because ``v2.0.0`` was never pushed (verify pre-move with ``git ls-remote --tags origin v2.0.0`` returning empty). |
| **B (fallback)** | Rename the existing local ``v2.0.0`` to ``v2.0.0-dev`` (or leave it as-is at ``6905ecd4``) and mint a fresh ``v2.0.0`` on the merge commit. Two tags, two hashes. | Cleaner audit (no tag-move); but reviewers must learn that ``v2.0.0-dev`` is internal-only and ``v2.0.0`` is canonical. Adds release-notes friction. |

**Recommendation**: **A**. Single tag = single release point;
the merge commit is what users ``git checkout v2.0.0`` to
inspect. The existing ``v2.0.0`` tag at ``6905ecd4`` was an
internal "P-RC-9 closed" marker -- it never escaped to a remote
and has no external consumers. Operator may still override to B
if a tag-move audit trail is preferred over a clean ``v2.0.0``
HEAD.

## §3 Merge mechanics

### Recommended command

```bash
git checkout main
git merge --no-ff revamp/v3-orgs \
  -m "Merge revamp/v3-orgs into main: v2.0.0 backend revamp (P-RC-0 .. P-RC-10) [v2.0.0]"
```

* ``--no-ff`` (mandatory): forces a single merge commit even though the branch is fast-forwardable. The merge commit becomes the epic boundary on ``main`` and is the trivial rollback point -- ``git revert -m 1 <merge-hash>`` undoes the entire epic in one commit (see §5).
* **DO NOT use ``--squash``**: the per-phase commit history (P-RC-0 .. P-RC-10, ~378 commits) is the audit substrate that every G-RC-N gate doc cites by hash. Squashing erases the trail and breaks the gate cross-references (G-RC-9 §3, G-RC-10 §3 commit roll-ups).
* **DO NOT rebase** ``revamp/v3-orgs`` onto ``main`` first. ``main`` has not advanced since the fork (``0    378`` ledger), so a rebase is a no-op that would only churn commit hashes and invalidate the gate cross-references.

### Pre-merge checklist (operator runs these; ALL must pass)

* [ ] **Narrow slice green**: ``pytest tests/parity/orgs/ tests/api/contracts/ tests/runtime/orgs/ -q`` -> **459 passed** (267 parity+contracts + 192 runtime/orgs; baseline byte-stable through every P-RC-10 phase commit).
* [ ] **v2 IM canary 3x within +-5% of 1.92 s baseline**: ``pytest tests/integration/test_v2_im_canary_e2e.py`` run three consecutive times; record p50 / p95 each run. Per G-RC-10 §2 row 6 this clears DEFERRED.
* [ ] **Playwright e2e green**: ``apps/setup-center/e2e/v2-orgs-flow.spec.ts`` (and any other v2 orgs Playwright spec) green on the pre-merge HEAD. Per G-RC-10 §2 row 9 this clears DEFERRED.
* [ ] **Import-time clean**: ``python -c "from openakita.api.server import create_app; create_app()"`` exits 0 with **zero ``DeprecationWarning`` from ``openakita.runtime.orgs``** (the shim was removed at P10.6 / ``cea93777``; this is a regression sentinel, not just a smoke check).
* [ ] **Sentinels (#1 .. #9) green**: ``pytest tests/parity/orgs/test_*sentinel*.py -q`` -> all green; ``test_v1_src_retired_sentinel.py`` Test 2 must remain at no-whitelist after P10.6 hardening.
* [ ] **G-RC-10 verdict flipped from PROVISIONAL -> PASS** by operator (see §7 row 4).

If any row fails, **DO NOT MERGE**. File a P10.7c (or roll into
the P-RC-11 candidate) and return.

## §4 Release notes draft (CHANGELOG seed for v2.0.0)

* **Backend revamp complete (P-RC-0 .. P-RC-10)**: 378 commits, 722 files changed, +94 222 / -43 717 LOC.
* **Org runtime namespace flattened**: ``openakita.runtime.orgs.*`` -> ``openakita.orgs.*`` (atomic ``git mv`` at P10.1; 124 in-tree call sites swept across 71 files at P10.3a..f). The transitional ``openakita.runtime.orgs`` import path is now a fail-loud ``ModuleNotFoundError`` (shim deleted at P10.6); any external code on the legacy path will break **by design** (sentinel #9 enforces).
* **v1 ``orgs`` surface fully retired (P-RC-9)**: ``src/openakita/orgs/`` (legacy v1 subsystem), ``api/routes/orgs.py`` (v1 router; replaced by 410 Gone semantics per Q-B), and ``tests/orgs/`` deleted.
* **v2 contract surface stable**: 267 parity + contract cases (262 P9.9 baseline + 5 strategic v2 cases at P10.5c) + 192 runtime/orgs cases = **459 narrow-slice cases**; OpenAPI snapshot byte-stable.
* **308 redirect shim STILL ACTIVE in v2.0.0**: legacy v1 ``/api/orgs/*`` paths continue to 308-redirect to canonical ``/api/v2/orgs-spec/...``. Per **ADR-0015 option (b)** the shim retires in **v2.1.0** (see §6); v2.0.0 keeps it byte-untouched.
* **SSE event store + watchdog**: durable SSE state for org-runtime events; watchdog reaps stale streams.
* **Lifecycle state machine + sentinel #9** (``test_v1_src_retired_sentinel.py``): two-test sentinel guarding ``src/openakita/runtime/orgs/`` non-existence and banning ``openakita.runtime.orgs.*`` imports -- no whitelist after P10.6.
* **5 deferred nits CLOSED** (P10.5a..e + P10.5f roster): M-2 (ADR-0014 per-shard cap split), P9.7-B (test conftest hoist), epsilon-O1 (5 strategic v2 contract cases), epsilon-O2 (monitor + back-fill disposition), GroupC (frontend stale ``/api/orgs/*`` literals removed).
* **Plugin reseed CLI**, **LLM latency benchmark**, and **ruff hygiene** (line-length / target-py311) applied across the epic.
* **Carry-over (NOT in v2.0.0; tracked for P-RC-11 candidate)**: 60 pre-existing test failures (17 ``tool_categories`` missing-module, 22 ``state_graph/guards`` circular-import, 3 308 smoke 503, 4 ``test_policy_v2_*`` static-grep, 2 telegram env, 5 misc, 5 collection errors). All documented in G-RC-10 §6 as out-of-epic.
* **No v1 IM channel changes**; channels layer untouched in P-RC-10.

Operator: prune / reorder / re-word for the public CHANGELOG;
this seed is a faithful technical inventory, not marketing copy.

## §5 Rollback procedure

* **One-liner**: ``git revert -m 1 <merge-commit-hash>`` produces a single revert commit on ``main`` that undoes the entire epic. ``-m 1`` selects the first parent (= pre-merge ``main`` tip) as the mainline.
* **Branch retention**: keep ``revamp/v3-orgs`` alive on ``origin`` for **30 days** post-merge for cherry-picks, forensic comparison, and the rollback-plus-reapply path. After 30 days quiet, the branch may be deleted (recoverable via ``git reflog`` for another ~30 days).
* **Tag retention**: under §2 option A, the moved ``v2.0.0`` tag survives a ``git revert`` (the tag points at the merge commit; revert leaves the merge commit in place and adds a new "Revert ..." commit on top). Operator may choose to delete-and-re-mint ``v2.0.0`` post-revert.
* **Escalation criteria** (revert vs hotfix-forward):
  - **Revert**: 3+ user-reported regressions in the first 7 days that do **not** have a <=24h patch path; OR any single security-critical regression with no patch.
  - **Hotfix-forward**: 1-2 regressions with clear <=24h fixes -- accumulate into v2.0.1 (see §6) instead.
* **No ``release/2.0.x`` lane by default**: single forward-moving ``main``. Cherry-pick to an ad-hoc ``hotfix/2.0.x`` branch only if §6 v2.0.1 cycle accumulates faster than ``main`` can absorb. Operator may override.

## §6 Post-merge milestones

| milestone | trigger / criteria | scope |
|---|---|---|
| **v2.0.0** (this merge) | §3 checklist all green + §7 row 4 sign-off + ``git merge --no-ff`` lands + §2 tag minted | Epic merge; operator burn-in **>= 7 days** (per P9.9 directive). |
| **v2.0.1** (as needed) | Non-critical hygiene + nit-fixes accumulate during burn-in | One minor bump cycle; NO shim retirement here. |
| **v2.1.0** (planned) | v2.0.0 stable >= 1 week + zero open P-RC-10 nits + operator approval | Retire 308 shim per **ADR-0015** option (b): ``git rm api/routes/_orgs_v2_legacy_redirects.py`` + drop mount in ``api/server.py`` + regenerate OpenAPI snapshot (sentinel #7 forcing function) + sweep sentinel #8 allowlist drift. |
| **P-RC-11 candidate** | Opens **after** v2.1.0 ships | Absorbs G-RC-10 §6 carry-overs: ``tool_categories`` missing module (17 cases), core/agent/llm circular import family (22+ cases), telegram env smoke (2 cases), static-grep stale references in ``test_policy_v2_*`` (4 cases). Plus archived Category B/C ``runtime/{llm,io,context,desktop,guardrail,state_graph,nodes,templates,backends}`` triage. |

## §7 Operator decision matrix

Four explicit decisions are required from the operator before
§3 merge mechanics fire:

| # | decision | options | default | ratifies |
|--:|---|---|---|---|
| 1 | **Tag strategy** | A (move local ``v2.0.0`` to merge commit) / B (mint fresh ``v2.0.0`` on main, keep dev tag at ``6905ecd4`` as ``v2.0.0-dev``) | **A** | §2 |
| 2 | **Merge timing window** | "now" (next operator session) / specific calendar date / hold-on-fix | operator picks | §3 checklist |
| 3 | **Release notes final wording** | Accept §4 seed verbatim / edit-then-commit / replace wholesale | accept §4 seed (technical inventory) | §4 |
| 4 | **G-RC-10 PROVISIONAL -> PASS sign-off** | Sign / Hold | **must sign before merge**; sign-off requires §3 checklist all green | G-RC-10 verdict |

## §8 Acceptance criteria for merge (clearing G-RC-10 DEFERRED rows)

G-RC-10 §2 marked the following rows DEFERRED-TO-P10.7b. Each
row's clearance gate is the corresponding §3 checklist line:

| G-RC-10 §2 row | criterion | how P10.7b clears it |
|--:|---|---|
| 6 | v2 IM canary 3x within +-5% of 1.92 s baseline | §3 checklist row 2 (operator runs the 3x canary battery; record p50/p95 in commit message of the §7 row 4 sign-off commit). |
| 9 | Playwright e2e green | §3 checklist row 3 (operator runs ``apps/setup-center/e2e/v2-orgs-flow.spec.ts``; capture summary). |
| 10 | G-RC-10 final mini-gate signs PASS | §7 row 4 sign-off commit edits ``gates/G-RC-10.md`` verdict block PROVISIONAL -> PASS (small docs-only commit; no source touched). |
| 11 | Merge-to-main plan ratified by operator | This document (when accepted via §7 rows 1-3) plus the §3 merge commit landing on ``main``. |

Once rows 6 / 9 / 10 / 11 are SATISFIED, **all 11** G-RC-10 §2
acceptance rows are SATISFIED and the gate is no longer
PROVISIONAL.

## §9 Cross-references

* **ADR-0015** (``docs/adr/0015-308-shim-retirement-governance.md``) -- 308 shim retirement at v2.1.0 (option (b) LOCKED). This charter respects that lock and does NOT touch the shim in v2.0.0.
* **G-RC-10** (``docs/revamp/gates/G-RC-10.md``) -- PROVISIONAL final gate. This charter is the missing-piece P10.7b that flips the verdict to PASS once §7 row 4 is signed.
* **P-RC-10 charter** (``docs/revamp/P-RC-10-CHARTER.md``) **§4** -- merge-to-main skeleton; this document promotes that skeleton to a first-class operator charter.
* **Progress ledger** (``docs/revamp/PROGRESS_LEDGER_P10.md``) -- one row per P-RC-10 phase commit; this charter appends the P10.7b closure row.
* **P-RC-9 ancestor** (``docs/revamp/gates/G-RC-9.md``) -- direct ancestor; supplies template + the 5 deferred nits P-RC-10 cleared.
* **P-RC-11 candidate** -- opens post-v2.1.0; absorbs G-RC-10 §6 carry-overs (see §6).

---

**Verdict footer**: P10.7b ratifies the plan only. The actual
``git merge``, the ``v2.0.0`` tag mint, and the G-RC-10
PROVISIONAL -> PASS flip are **operator-driven** actions taken
in a subsequent session. **This commit does NOT execute merge /
tag / push / branch switch.**
