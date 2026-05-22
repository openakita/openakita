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
