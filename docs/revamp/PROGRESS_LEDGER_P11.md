# P-RC-11 progress ledger

Per-commit ledger for the P-RC-11 epic (carry-over absorption
of the 60 G-RC-10 section 6 failures). Sibling to
``PROGRESS_LEDGER_P10.md`` (P-RC-10) and
``PROGRESS_LEDGER_P9.md`` (P-RC-9); same per-epic-file
cadence to keep individual ledger files reviewable.

**Branch**: ``revamp/v3-orgs``. **Ancestor gate**: G-RC-10
PROVISIONAL (parent epic CLOSED on all in-epic axes; sealed
once the operator runs the merge per
``docs/revamp/MERGE_TO_MAIN_v2.md`` section 3). **Charter**:
``docs/revamp/P-RC-11-CHARTER.md`` -- ratified at P11.0a
below.

Each ## entry records: sub-phase, headline, scope summary,
test-evidence delta, hard-rule compliance footnote, and a
single-row summary table at the end.

---

## P11.0a -- P-RC-11 charter ratified (carry-over absorption epic, promoted ahead of merge)

> **Sub-phase status (2026-05-22, P11.0a LANDED)**: Mints
> ``docs/revamp/P-RC-11-CHARTER.md`` as a fresh charter
> (358 LOC; 5 numbered sections + 1 prioritisation sub-table)
> for the P-RC-11 carry-over absorption epic. The user has
> **promoted** P-RC-11 from its archived "after v2.1.0 ships"
> slot (per ``P-RC-10-CHARTER.md.archived`` section 0 +
> P-RC-10 charter section 7 P-RC-11-candidate row) to
> **execute BEFORE the ``revamp/v3-orgs -> main`` merge
> lands** so v2.0.0 ships with a cleaner test baseline
> (full-suite 6026 / 60 carry-overs at G-RC-10 PROVISIONAL
> -> target >= 6080 / <= 6 residual at G-RC-11 PASS).
>
> **Charter shape** (mirrors P-RC-10's P10.0a layout but
> with content scoped to the 7-cluster carry-over inventory
> from G-RC-10 section 6): section 0 executive summary;
> section 1 epic goals + non-goals (the 308 shim retirement
> stays LOCKED to v2.1.0 per ADR-0015 -- in scope of
> P-RC-11 is the **xfail-pinning** of the 3 affected smoke
> tests, NOT the shim retirement itself); section 2
> sub-phase breakdown P11.0a..P11.7a (8-11 commits over
> ~3-5 days; per-cluster envelope ranges from +5 LOC
> Cluster C xfail to +155 LOC Cluster A tool_categories
> recovery); section 3 acceptance criteria (7 rows;
> mirrors G-RC-10 section 2's row-by-row pattern); section
> 4 risk register (5 risks) + 4.1 cluster prioritisation
> table (highest-value first: A -> B+G -> D -> C -> E ->
> F); section 5 cross-references (G-RC-10 / P-RC-10-CHARTER
> / P-RC-10-CHARTER.md.archived / MERGE_TO_MAIN_v2.md /
> ADR-0011 / ADR-0014 / ADR-0015 / sibling P-RC-11-RECON.md
> + this ledger).
>
> **Cluster inventory pre-baked into section 2** (recon
> doc P11.0b will verify each via ``git grep`` + read,
> not speculate):
>
> * Cluster A -- ``openakita.orgs.tool_categories`` deleted
>   at P9.9 epsilon-2b ``90a7d77f``; 4 in-tree callers
>   stranded; 17 + 1 = 18 tests fail. P11.1 restores as
>   ``_runtime_tool_categories.py`` shard.
> * Cluster B + G -- ``core.errors`` -> ``agent.__init__``
>   -> ``agent.brain`` -> ``core._brain_legacy`` ->
>   ``llm.client`` -> ``core.errors`` circular (verified by
>   direct ``python -c "import openakita.core.
>   _reasoning_engine_legacy"`` probe at this charter's
>   authorship). 22 + 3 + 2 = 27 tests fail / error. P11.2
>   converts ``agent/__init__.py`` to PEP-562
>   ``__getattr__`` lazy loader (or inlines
>   ``UserCancelledError`` in ``core/errors.py``).
> * Cluster C -- 3 ``test_p97_alpha2_smoke`` 308-shim
>   tests fail with 503; 308 shim hard-rule LOCKED per
>   ADR-0015 -- P11.3 ``@pytest.mark.xfail(strict=True,
>   reason="...ADR-0015...")``.
> * Cluster D -- 4 ``test_policy_v2_*`` tests static-grep
>   ``src/openakita/core/agent.py`` which was renamed to
>   ``_agent_legacy.py`` in commit ``32c29c54`` (long
>   pre-P-RC-10). 3 are direct path reads; 1 is collateral
>   damage of Cluster B (self-clears once P11.2 lands).
> * Cluster E -- 2 ``test_telegram_simple`` tests fail with
>   ``InvalidToken`` because ``TELEGRAM_BOT_TOKEN`` env-var
>   is set but invalid; the existing
>   ``pytest.skip(allow_module_level=True)`` only checks
>   presence, not validity. P11.5 tightens predicate.
> * Cluster F -- 5 misc legacy unit failures (``test_c17_*``,
>   ``test_c23_*``, ``test_memory_manager``, residual
>   ``TestGetResources`` cases after Cluster A absorbs the
>   tool_categories one). P11.6 case-by-case.
>
> **What this commit does NOT do (hard stop)**: ZERO source
> edits, ZERO test edits, ZERO sentinel touches, ZERO ADR
> edits, ZERO touch on ``api/routes/
> _orgs_v2_legacy_redirects.py`` (the 308 shim), ZERO touch
> on ``MERGE_TO_MAIN_v2.md`` (P10.7b's authority), ZERO
> touch on ``P-RC-10-CHARTER.md`` / ``P-RC-10-RECON.md`` /
> ``gates/G-RC-10.md`` (parent epic surface). Charter is
> ratified by docs alone; recon (P11.0b) and execution
> (P11.1..P11.7a) ride subsequent commits.
>
> **Hard-rule compliance**: only
> ``docs/revamp/P-RC-11-CHARTER.md`` (NEW; 358 LOC) +
> ``docs/revamp/PROGRESS_LEDGER_P11.md`` (NEW; this file)
> modified. BOM-free tempfile via Python
> ``open(..., encoding='utf-8')`` (no BOM by default; LF
> newlines forced via ``newline='\n'``). Pre-flight verify:
> ``[System.IO.File]::ReadAllBytes(...).Take(3)`` returns
> ``23-20-50`` (= ``# P``), not ``EF-BB-BF`` (BOM).
>
> Next: P11.0b (P-RC-11 reconnaissance doc); separate
> commit, same docs-only envelope. P11.1 (Cluster A)
> opens once recon lands.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-11 P11.0a | docs(revamp): P11.0a draft P-RC-11 charter for G-RC-10 carry-over absorption epic [P-RC-11 P11.0a] | +358 P-RC-11-CHARTER.md (NEW) + ~+85 ledger seed (NEW) = ~+443 docs-only | unchanged (zero source / test edits in this commit; G-RC-10 baseline = 6026 passed / 60 carry-overs target) | ADR-0015 (308 shim retirement; respected as LOCKED -- shim NOT touched) + cross-refs to ADR-0011 / ADR-0014 (informational only; no ADR file edits) |


---

## P11.0b -- P-RC-11 recon doc landed (per-cluster carry-over inventory)

> Mints `docs/revamp/P-RC-11-RECON.md` (434 LOC) --
> 7-cluster companion to P11.0a charter; per cluster
> (A..G): failing tests, source files, root cause
> (grep-verified at `5b32d845`), fix strategy, LOC,
> dependencies. Section 8 endorses charter 4.1 ordering
> (A -> B+G -> D -> C -> E -> F); section 9 confirms
> ~+1 330 net envelope. Supplement: `agents/profile.py`
> imports `tool_categories` (beyond charter R-11-2);
> P11.1 re-export handles it. BOM-free; docs-only. Next: P11.1.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-11 P11.0b | docs(revamp): P11.0b add P-RC-11 reconnaissance doc with per-cluster carry-over inventory [P-RC-11 P11.0b] | +434 P-RC-11-RECON.md (NEW) + ~+18 ledger row = ~+452 docs-only | unchanged (baseline 6026 / 60 carry-overs) | ADR-0015 (Cluster C xfail; LOCKED) + ADR-0011 / ADR-0014 (informational) |

---

## P11.1 -- Cluster A landed (`openakita.orgs.tool_categories` private shard restored)

> **Sub-phase status (2026-05-22, P11.1 LANDED)**: Re-instates
> the v1 ``openakita.orgs.tool_categories`` module that
> P9.9 epsilon-2b ``90a7d77f`` atomic-deleted alongside the
> rest of ``src/openakita/orgs/`` (26 files / 20 237 LOC).
> The deletion stranded 4 in-tree callers
> (``agents/factory.py:370``, ``agents/profile.py:129``
> comment, ``orgs/_runtime_templates.py`` 6 comment-only
> sites, ``tools/handlers/org_setup.py:129/440/731``) and
> caused 17 ``tests/unit/test_org_setup_tool.py`` failures
> + 1 ``TestGetResources::test_returns_tool_categories``
> case G-RC-10 mis-bucketed under Cluster F (= 18 cleared
> here per charter section 2 P11.1).
>
> **Restoration** -- charter R-11-2 option (b) ratified at
> recon section 1.4 step 2:
>
> 1. New private shard
>    ``src/openakita/orgs/_runtime_tool_categories.py``
>    (188 LOC = 16-line restoration banner + 172-line v1
>    body restored verbatim from
>    ``git show 90a7d77f~1:src/openakita/orgs/tool_categories.py``).
>    Naming follows P10.5a M-2 split convention; ADR-0011
>    6-subsystem layout. Symbols restored: ``TOOL_CATEGORIES``,
>    ``ROLE_TOOL_PRESETS``, ``ALL_CATEGORY_NAMES``,
>    ``expand_tool_categories``, ``get_preset_for_role``,
>    ``list_categories``, ``AVATAR_PRESETS``, ``AVATAR_MAP``,
>    ``get_avatar_for_role``, ``list_avatar_presets`` (10
>    public + 2 private ``_ROLE_KEYWORDS`` /
>    ``_ROLE_AVATAR_KEYWORDS``).
> 2. New 9-LOC public re-export shim
>    ``src/openakita/orgs/tool_categories.py`` =
>    ``from ._runtime_tool_categories import *  # noqa: F401,F403``
>    (preserves the v1 public import path; the 4 known
>    callers stay byte-untouched per charter R-11-2 mitigation
>    "post-commit static grep ``git grep tool_categories`` zero
>    outside the new shard").
>
> **Test evidence** (``revamp/v3-orgs`` HEAD pre-commit):
>
> * Target file ``tests/unit/test_org_setup_tool.py``:
>   **58 / 58 passed in 2.63 s** (was 41 passed / 17 failed at
>   G-RC-10; all 17 cleared, plus the 1 absorbed
>   ``TestGetResources::test_returns_tool_categories`` case).
> * Narrow slice
>   ``tests/parity/orgs/ + tests/api/contracts/ + tests/runtime/orgs/``:
>   **459 / 459 passed in 77.10 s** -- byte-identical to the
>   G-RC-10 narrow-slice baseline (acceptance criterion 4 holds).
> * Backend boot smoke:
>   ``python -c "from openakita.api.server import create_app; create_app()"``
>   **succeeds** (417 routes mounted; sentinels #1..#9 untouched
>   so OpenAPI byte-stable per acceptance criterion 5).
> * ``ruff check`` on the 2 new files: **All checks passed!**
>
> **Post-fix invariant verified** (recon section 1.5):
> ``git grep --untracked tool_categories -- src/`` returns
> hits in exactly the new shard + the new public re-export +
> the 4 callers (factory.py 2 sites, profile.py 1 comment,
> _runtime_templates.py 6 comment-only sites,
>  org_setup.py 5 sites including 3 imports + 2 ``result["tool_categories"]``
> dict-write lines) -- **zero stragglers** outside this set.
>
> **What this commit does NOT do (hard stop)**: ZERO touch on
> ``src/openakita/core/`` / ``src/openakita/agent/`` /
> ``src/openakita/llm/`` (concurrent Cluster B+G worker
> territory per task brief), ZERO test edits, ZERO sentinel /
> ADR / charter / recon / gate edits, ZERO touch on
> ``api/routes/_orgs_v2_legacy_redirects.py`` (308 shim) or
> ``MERGE_TO_MAIN_v2.md``. The 4 caller sites stay
> byte-identical (no ``re.sub`` rewrite needed; charter
> R-11-2 (b) deliberately routes through the public shim).
>
> **Hard-rule compliance**: only
> ``src/openakita/orgs/_runtime_tool_categories.py`` (NEW;
> 188 LOC) + ``src/openakita/orgs/tool_categories.py`` (NEW;
> 9 LOC) + ``docs/revamp/PROGRESS_LEDGER_P11.md`` (this row;
> ~+85 LOC) modified. Both source files written with
> ``pathlib.Path.write_bytes(text.encode('utf-8'))`` (no BOM,
> LF newlines); post-write verify
> ``b[:3] == b'\xef\xbb\xbf'`` returns ``False`` and
> ``b'\r' in b`` returns ``False`` for both new files.
>
> Next: P11.2 (Cluster B + G -- ``core.errors`` circular
> import; ~+10 LOC) opens in parallel-safe slot once
> operator green-lights the concurrent Cluster B+G worker.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-11 P11.1 | feat(orgs): P11.1 restore openakita.orgs._runtime_tool_categories private shard (cluster A; +17-18 passing tests) [P-RC-11 P11.1] | +188 _runtime_tool_categories.py (NEW) + +9 tool_categories.py public shim (NEW) + ~+85 ledger row = ~+282 (charter envelope ~+155; banner + ledger row drove the overrun, body itself restored verbatim 172 LOC) | +18 passed (17 ``test_org_setup_tool.py`` Cluster A + 1 ``TestGetResources::test_returns_tool_categories`` absorbed from F); narrow slice 459 / 459 unchanged; backend boot OK | ADR-0011 (6-subsystem layout; new shard slots in cleanly) + ADR-0014 (per-shard soft cap; 188 LOC well within budget) -- both informational, no ADR edits |

## P11.2 -- Cluster B / G structural fix (core.errors re-export cycle broken)

> P11.2 lands the structural fix recommended by recon section 2.4
> for the `core.errors` <-> `agent.errors` re-export cycle.
> The cycle was the original carry-over diagnosis for Cluster B (22
> failing tests in `tests/runtime/state_graph/guards/`) and Cluster
> G (3 errors in `tests/runtime/state_graph/guards/test_tool_filters.py`
> + 2 collection errors in the `tests/unit/test_action_claim_*_guard.py`
> family).
>
> **Cycle diagnosis (verified by adversarial probe)**: `python -c
> "import openakita.core._brain_legacy" ` reproduces the failing
> import chain `core._brain_legacy -> llm.client -> core.errors ->
> agent.errors -> agent.__init__ -> agent.brain -> core._brain_legacy`
> exactly as recon predicted, raising `ImportError: cannot import name
> 'Brain' from partially initialized module
> 'openakita.core._brain_legacy'`.  A parallel cycle re-enters
> `core.errors` mid-load through `agent.__init__ -> .core ->
> core._agent_legacy -> .errors`.
>
> **Strategy chosen**: function-local imports (per task brief
> preference over `TYPE_CHECKING`), applied at two cycle-closers:
>
> 1. `src/openakita/core/_brain_legacy.py`: the module-level
>    `from ..llm.client import LLMClient` (line 21) is the closer for
>    the brain branch.  Moved to four method-local imports inside the
>    methods that actually instantiate or reference `LLMClient` --
>    `__init__`, `_init_compiler_client`, `reload_compiler_client`,
>    `think_lightweight_stream` (alongside the existing function-local
>    `from .stream_accumulator import StreamAccumulator`).  Net +3 LOC.
> 2. `src/openakita/core/errors.py`: the module-level
>    `from openakita.agent.errors import UserCancelledError` (line 15)
>    is the closer for the agent branch.  Rewritten as a PEP 562
>    `__getattr__` (still a function-local import, just hosted inside
>    the module-level `__getattr__` hook).  `core.errors` now loads
>    without dragging in the `agent` package; `UserCancelledError` is
>    resolved on first attribute access (and cached in `globals()`),
>    preserving `core.errors.UserCancelledError is
>    agent.errors.UserCancelledError` class identity.
>
> Both edits keep the ADR-0003 ownership boundary intact (`agent.errors`
> remains canonical; `core.errors` remains a shim).  No re-home; no
> behaviour change for any runtime caller.
>
> **Cycle status -- after fix**: `python -c
> "import openakita.core._brain_legacy, openakita.llm.client,",
> `openakita.core.errors, openakita.agent, openakita.agent.brain,`
> `openakita.core._agent_legacy, openakita.core._reasoning_engine_legacy`
> succeeds, and `core.errors.UserCancelledError is
> agent.errors.UserCancelledError` returns `True`.
>
> **Test deltas (honest report)**:
>
> * Cluster B target -- `tests/runtime/state_graph/guards/`:
>   91 passed / 21 failed / 3 errors **before** the patch;
>   91 passed / 21 failed / 3 errors **after** the patch (set-identical
>   failure list; verified by `Compare-Object` on sorted FAILED/ERROR
>   lines from the two full-suite logs).
> * Cluster G target -- `test_tool_filters.py` errors + the
>   `test_action_claim_*_guard.py` collection errors: ignored from the
>   delta run per task brief; the 3 `test_tool_filters.py` errors are
>   the same 3 `AttributeError: module
>   'openakita.core._reasoning_engine_legacy' has no attribute
>   '_get_mode_ruleset'` setup errors before and after.
> * Full suite (`pytest tests/ --ignore=tests/e2e
>   --ignore=tests/unit/test_action_claim_guard.py
>   --ignore=tests/unit/test_action_claim_recap_guard.py`):
>   **33 failed, 6048 passed, 103 skipped, 6 deselected, 5 xfailed, 3
>   errors** -- byte-identical to baseline.  Net pass delta = **0**.
> * Narrow slice `tests/parity/orgs/ + tests/api/contracts/ +
>   tests/runtime/orgs/`: **459 / 459 passed** -- unchanged from the
>   P11.1 baseline.
>
> **Why the +0 test delta despite the cycle being real**: the cycle is
> order-dependent.  It only fires when `core._brain_legacy` (or another
> sibling) is imported *before* `agent.brain`.  In the pytest suite,
> conftest fixtures and earlier collection load `openakita.agent`
> first, so the cycle never triggers during test execution.  Recon
> section 2.1 cites this as a hypothesis ("exposed every time a test
> fixture imports ... _reasoning_engine_legacy or any sibling that
> pulls openakita.agent early") but the empirical test-collection order
> on the current branch does not match that pattern.  The cluster B / G
> test failures still have a real root cause -- the legacy aliases
> `_is_recap_context` and `_get_mode_ruleset` that the parity tests
> import from `openakita.core._reasoning_engine_legacy` no longer
> exist (verified by `git log -p` showing they were removed as part of
> an earlier reasoning-engine slim-down).  Restoring those
> module-level re-exports is a separate, follow-up unit of work
> (out-of-scope for P11.2; touches the same legacy file but is a
> different fix and a different test surface).
>
> **What this commit does NOT do (hard stop)**: ZERO touch on
> `src/openakita/orgs/` (concurrent Cluster A worker territory per
> the parallel-safety brief), ZERO test edits, ZERO sentinel / ADR /
> charter / recon / gate edits, ZERO push, ZERO tag.  Only
> `src/openakita/core/_brain_legacy.py` (+3 LOC net) and
> `src/openakita/core/errors.py` (-1 module-level import +
> ~15 LOC PEP 562 hook / docstring update; net ~+14 LOC) and this
> ledger row are modified.  Both source files written via
> `pathlib.Path.write_bytes(text.encode('utf-8'))` (no BOM,
> CRLF preserved for `_brain_legacy.py` matching its original
> line-ending); post-write probe confirms `b[:3] != b'\xef\xbb\xbf'`
> for both.
>
> Next: parallel Cluster A worker continues; a separate follow-up
> commit can restore the missing `_is_recap_context` /
> `_get_mode_ruleset` legacy aliases to `_reasoning_engine_legacy.py`
> to actually clear the Cluster B / G test failures (the cycle fix here
> unblocks that work by removing a latent foot-gun that would otherwise
> trip any future caller importing `core._brain_legacy` first).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-11 P11.2 | fix(core,agent,llm): P11.2 break core/agent/llm circular import (clusters B + G; +0 passing tests, cycle exposes pre-existing missing legacy aliases) [P-RC-11 P11.2] | +3 _brain_legacy.py (4 function-local imports - 1 module-level) + ~+14 errors.py (PEP 562 hook + docstring update - 1 eager import); ~+90 ledger row = ~+107 (charter envelope ~+10 source LOC respected; ledger row drove the overrun) | +0 passed (full-suite 6048 / 6048 byte-identical; narrow slice 459 / 459 unchanged; cluster B / G failures unchanged because their real root cause is missing legacy aliases `_is_recap_context` / `_get_mode_ruleset`, not the cycle -- recon section 2.1 hypothesis re-examined empirically) | ADR-0003 (`UserCancelledError` ownership stays at `agent.errors`; PEP 562 hook is a structural fix, not a re-home) -- informational, no ADR edits


## P11.3 ledger -- 2026-05-22

> Cluster C (308 redirect shim smoke; 3 cases in
> `tests/api/test_p97_alpha2_smoke.py`) marked xfail per
> ADR-0015 option (b): the 308 -> mint shim retirement is
> locked for v2.1.0; the 503 the shim returns in v2.0.0 is
> the spec-compliant behaviour, not a regression. `strict=False`
> per task brief so the day the shim retires and the tests start
> passing again the suite stays green automatically; the v2.1.0
> retirement PR removes the decorators in the same commit.
>
> Targets (3 decorators, 1-line each):
> * `test_legacy_patch_org_returns_308` (line 116)
> * `test_legacy_stream_returns_308` (line 139)
> * `test_redirect_preserves_query_string_for_unclaimed_path`
>   (line 145)
>
> Verification: `pytest tests/api/test_p97_alpha2_smoke.py
> -q --tb=no -rxX` -> `12 passed, 3 xfailed` (was `12 passed,
> 3 failed`). Narrow slice `tests/parity/orgs/ +
> tests/api/contracts/ + tests/runtime/orgs/` =
> `459 / 459 passed` unchanged.
>
> ZERO source / sentinel / ADR / gate / charter / recon edits;
> only the 3 test decorators + this ledger row.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-11 P11.3 | test(api): P11.3 mark 3 v1 308-shim smoke tests xfail pending v2.1.0 retirement (cluster C; ADR-0015) [P-RC-11 P11.3] | +3 decorator lines in `tests/api/test_p97_alpha2_smoke.py` + ledger row; net +3 test LOC | -3 failed / +3 xfailed in cluster C narrow run; narrow slice `459 / 459` unchanged | ADR-0015 (option (b) v2.1.0 retirement lock) -- informational, no ADR edits |


## P11.4 ledger -- 2026-05-22

> Cluster D (test_policy_v2_* static-grep stale paths; 4 cases)
> closed by repointing test path strings to the post-flatten
> canonical locations:
>
> * `tests/unit/test_policy_v2_c8b3_apply_resolution.py`
>   `::TestCallsiteMigrationStatic::test_agent_cleanup_migrated`
>   -- `self._read("core/agent.py")` ->
>   `self._read("core/_agent_legacy.py")` (rename trail:
>   `32c29c54` -> `3d43af41` -> `a21cdd4b`; latter deleted
>   the shim leaving `_agent_legacy.py` as the only artifact).
> * `tests/unit/test_policy_v2_c8b5_trust_mode_isolation.py`
>   `::TestExternalCallersGone::test_agent_py_no_v1_is_trust_mode_call`
>   -- `(SRC_ROOT / "core" / "agent.py")` ->
>   `(SRC_ROOT / "core" / "_agent_legacy.py")`; invariants
>   (`getattr(engine, "_is_trust_mode"` absent, etc.) still hold
>   in `_agent_legacy.py` (verified by `Select-String` --
>   only `policy_v2` imports present).
> * Same file `::test_check_trust_mode_skip_is_pure_v2` --
>   the function `check_trust_mode_skip` itself was extracted
>   out of `core/agent.py` into the canonical
>   `agent/safety/destructive_intent.py`;
>   `_agent_legacy.py` only carries an `as _check_trust_mode_skip`
>   re-export. Repoint reads `destructive_intent.py` and drops
>   the leading underscore from the regex; widens the v2-import
>   assertion to `"policy_v2 import ConfirmationMode"` so both
>   `from .policy_v2 ...` (relative) and
>   `from openakita.core.policy_v2 ...` (absolute, the canonical
>   form) match.
> * `tests/unit/test_policy_v2_c13_multi_agent.py`
>   `::test_tool_executor_security_confirm_marker_has_no_c13_fields`
>   -- recon section 4 / charter section 2 forecast this as Cluster B
>   collateral that would self-clear after P11.2; in practice the
>   failure surfaced as `ModuleNotFoundError: openakita.core.tool_executor`
>   (same rename pattern: `cd69cd60` rename + `8e8e7da7` shim +
>   `a21cdd4b` delete) -- fixed by the same string-edit
>   `openakita.core.tool_executor` -> `openakita.core._tool_executor_legacy`.
>   Cluster D scope thus absorbs this 4th case correctly.
>
> Verification: `pytest tests/unit/test_policy_v2_c8b3*
> tests/unit/test_policy_v2_c8b5* tests/unit/test_policy_v2_c13*
> -q --tb=line` -> `50 passed` (was `46 passed / 4 failed`;
> +4 passing). Narrow slice `tests/parity/orgs/ +
> tests/api/contracts/ + tests/runtime/orgs/` =
> `459 / 459 passed` unchanged.
>
> ZERO source / sentinel / ADR / gate / charter / recon edits;
> only the 3 test files + this ledger row.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-11 P11.4 | test(unit): P11.4 update test_policy_v2_* static-grep paths to post-flatten canonical locations (cluster D) [P-RC-11 P11.4] | +15 / -8 net +7 across 3 test files (c8b3 +1/-1, c8b5 +14/-6, c13 +1/-1); within ~+10/-10 charter envelope (R-11-3 LOC budget); ledger row not counted toward test LOC | +4 passed (46 -> 50 in the c8b3/c8b5/c13 trio); narrow slice `459 / 459` unchanged | ADR-0014 (post-flatten shard naming convention) -- informational, no ADR edits |
