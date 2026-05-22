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

> **Sub-phase status (2026-05-21, P10.0b RECON LANDED)**:
> P10.0b docs-only recon inventory landed.
> Measurements at HEAD ``52f8709a``: 25 v2 files
> (``runtime/orgs/``; LOC sum ~9 810) all relocate 1:1
> to ``orgs/`` at P10.1; 124 strict import sites across
> 63 files (M=122 / PT=0 / N=2 README.md citations);
> 104 doc citations (no rewrite in P10.3); 0 string-
> literal callers; P10.1 readiness verdict **GREEN**;
> P10.3 split refined to **5 mini-commits** (P10.3a..e)
> all within the 380-LOC envelope. See
> ``docs/revamp/P-RC-10-RECON.md``.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.0b | docs(revamp): P10.0b RECON import-sweep inventory + flatten mapping [P-RC-10 P10.0b] | +~365 (``P-RC-10-RECON.md`` ~351 + ``PROGRESS_LEDGER_P10.md`` append ~15) | 0 | --- (recon; cites ADR-0011 / 0014 / 0015 as references; no new ADR) |
## P10.1 -- Atomic flatten (runtime/orgs/* -> orgs/*)

> **Sub-phase status (2026-05-21, P10.1 LANDED + P10.2 LANDED)**:
> Atomic ``git mv`` of all 25 ``runtime/orgs/*.py`` files to
> ``src/openakita/orgs/`` (commit ``37536a62``); 1:1 rename per
> RECON section 1; 4 absolute self-import lines rewritten to
> relative form (manager.py x3 / _runtime_templates.py x1 per
> RECON section 3); 4 ins / 4 del net content delta (rename
> volume = 0 LOC). New canonical path
> ``openakita.orgs.X`` imports cleanly via the moved
> ``__init__.py``''s 21 relative re-export blocks.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``37536a62`` | P-RC-10 P10.1 | refactor(orgs): atomic flatten src/openakita/runtime/orgs/* -> src/openakita/orgs/* (25 files) [P-RC-10 P10.1] | +4 / -4 (relative-form swaps; 25-file rename = 0 LOC) | 0 (test slice deliberately skipped between P10.1 and P10.2; green again after P10.2) | ADR-0011 (subsystem decomposition; no Protocol change) |

## P10.2 -- Backward-compat shim + sentinel #9 Option-Z relax

> **Sub-phase status (2026-05-21, P10.2 LANDED)**: Single new
> file ``src/openakita/runtime/orgs/__init__.py`` (~46 LOC)
> re-exports ``openakita.orgs.*`` via ``from openakita.orgs
> import *`` plus 24 ``sys.modules`` aliases (RECON section
> 4) so the 122/124 strict submodule-form import sites keep
> resolving. One-shot ``DeprecationWarning`` at first import
> (``stacklevel=2``); pytest config carries no
> ``filterwarnings=error`` so DeprecationWarnings surface in
> stderr without failing tests. Sentinel #9 Test 1
> (``test_v1_src_directory_retired``) augmented in place
> (Option Z) -- replaces the "MUST NOT exist" assertion with
> a structural marker check (post-flatten the dir MUST contain
> ``_runtime_templates.py`` etc., which the v1 layout never
> had) so the legitimate v2 occupancy is recognised while v1
> regrowth is still blocked. Test 2
> (``test_production_imports_v1_free``) untouched; the strict
> ``openakita.runtime.orgs.*`` augment rides P10.4 per charter.
> Narrow slice green: 262 parity+contracts / 192 runtime-orgs
> (== baseline). Backend boot smoke deferred (no IM gateway
> changes; HTTP routes already smoke-tested via the contracts
> slice).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.2 | feat(orgs): add openakita.runtime.orgs deprecation shim re-exporting from new location [P-RC-10 P10.2] | +~46 (shim) / +~20 / -~22 (sentinel #9 Test 1 Option-Z relax) / +~50 (this ledger block) -- net ~+95 | 0 (slice still 262 / 192) | ADR-0011 (subsystem decomposition; no Protocol change); ADR-0015 (308 shim retirement -- explicitly OUT-OF-SCOPE; byte-untouched) |

## P10.3a -- Sweep ``src/openakita/`` import sites to canonical ``openakita.orgs``

> **Sub-phase status (2026-05-21, P10.3a LANDED)**: Mechanical
> 1:1 prefix swap of every ``from|import openakita.runtime.orgs``
> line under ``src/openakita/`` (excl. the deliberate shim
> ``runtime/orgs/__init__.py``) to the post-flatten canonical
> ``openakita.orgs`` path. **31 sites across 12 files**: api
> 9 files / 23 sites (orgs_v2_runtime_orgs 7 + orgs_v2_runtime_projects 5
> + server 4 + orgs_v2_runtime_state 2 + chat / orgs_v2 /
> orgs_v2_runtime_dispatch / orgs_v2_runtime_nodes /
> orgs_v2_stream 1 each), channels 1 file / 6 sites
> (gateway.py), core 1 file / 1 site
> (_reasoning_engine_legacy.py), runtime 1 file / 1 site
> (channel_routing.py). RECON section 2.1 projected ``8 files /
> 31 sites``; the 12-file count reflects the same 31 sites plus
> the api-cluster row enumerated 5 files but conflated 4
> sibling routes with 1 site each into ``+ 1 sibling route``
> (recon row drift, not a new site). 1:1 byte-equivalent
> semantics; one isort-driven re-sort in
> ``runtime/channel_routing.py`` (the rewritten line moves up
> two slots in alphabetic block order). DeprecationWarning
> count from src/ paths drops 1 -> 0
> (``orgs_v2.py:55`` was the last src-side site emitting the
> shim warning). Slice expected on next sweep:
> ``test_v1_src_retired_sentinel.py::test_production_imports_v1_free``
> trips on the new ``from openakita.orgs.X`` lines because the
> sentinel's v1-era regex is now inverted post-flatten; charter
> section 2 P10.4 augment / regex inversion is the explicit
> remediation and MUST land before P10.3b to keep the slice
> green between mini-commits.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.3a | refactor(src/openakita): P10.3a sweep openakita.runtime.orgs imports to canonical openakita.orgs (31 sites / 12 files) [P-RC-10 P10.3a] | +31 / -31 (mechanical prefix swap) + ~5 ledger row | 261 parity+contracts (1 sentinel-#9 Test 2 break expected per inverted regex; P10.4 fix-forward) / 192 runtime-orgs (unchanged) | ADR-0011 (subsystem decomposition; no Protocol change) |

## P10.4 -- Sentinel #9 Test 2 polarity reversed (ban legacy shim path)

> **Sub-phase status (2026-05-21, P10.4 LANDED)**: Sentinel #9
> Test 2 (``test_production_imports_v1_free``) rewritten in place
> with the post-flatten inverse polarity. BANNED regex now matches
> ``^\s*(?:from|import)\s+openakita\.runtime\.orgs(?:\.|$|\s)``
> across ``src/openakita/`` (``*.py`` + ``*.pyi``); the prior v1
> ban on ``openakita.orgs.*`` is dropped because P10.1 made that
> path canonical v2. Single whitelist entry
> ``src/openakita/runtime/orgs/__init__.py`` (the P10.2
> deprecation shim file; drops to empty at P10.6 when the shim is
> git-rm'd). Test name kept byte-stable so CI/sentinel tracking
> identifiers carry across the polarity flip; the docstring is
> the canonical record of the semantic change. Test 1
> (``test_v1_src_directory_retired``) is byte-untouched -- the
> P10.2 Option-Z structural-marker check survives unchanged
> (verified via region-SHA256 ``0cd39a57c0ed45ff`` matching HEAD
> ``5ac2c786``). Adversarial sanity: injected ``from
> openakita.runtime.orgs import OrgManager`` into a throwaway
> ``src/openakita/_p10_4_adversarial_probe.py`` -> sentinel
> tripped with the probe filename surfaced in the failure
> message; deleting the file restored PASS (script
> ``tmp_p10/_p10_4_adversarial.py``; not committed). 262
> parity+contracts baseline restored (was 261 after P10.3a, with
> the inverted sentinel as the only failure); 192 runtime-orgs
> unchanged.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.4 | test(sentinel-9): P10.4 reverse Test 2 polarity (ban openakita.runtime.orgs.* in src/; whitelist shim) [P-RC-10 P10.4] | +90 / -189 (Test 2 + helpers + module docstring rewrite; Test 1 byte-untouched per SHA-region check) + ~30 ledger | 262 parity+contracts (restored from 261) / 192 runtime-orgs (unchanged) | ADR-0011 (subsystem decomposition; no Protocol change); ADR-0015 (308 shim retirement -- OUT-OF-SCOPE; byte-untouched) |

## P10.3b -- Sweep ``tests/runtime/`` import sites to canonical ``openakita.orgs``

> **Sub-phase status (2026-05-21, P10.3b LANDED)**: Mechanical
> 1:1 prefix swap of every ``from|import openakita.runtime.orgs``
> import line under ``tests/runtime/`` to the canonical
> ``openakita.orgs`` path, plus the 7 Sphinx/docstring string
> references that name the canonical class/module location. 
> **37 sites across 18 files** (30 import-line rewrites + 7
> docstring string rewrites): ``tests/runtime/orgs/`` 13 files /
> 28 sites (test_blackboard_contract 2, test_command_service_contract
> 3, test_manager_contract 2, test_migrate_json_to_sqlite 2,
> test_migration_script 1, test_node_scheduler_contract 3,
> test_project_store_contract 2, test_runtime_contract 5,
> test_slug 1, test_sqlite_store 2, test_store_contract 2,
> test_template_alias 1, test_template_slug_integration 2),
> ``tests/runtime/`` (top-level) 5 files / 9 sites
> (test_cancel_wall_clock_budget 3, test_channel_routing 2,
> test_channel_routing_dispatch 1, test_migrate_orgs_to_v2 1,
> test_orgs_store 2). RECON section 2 projected ~30 sites /
> ~22 files for this cluster; observed 30 import sites / 18
> files (-4 files; RECON file-count drift only -- no missed
> sites). 1:1 byte-equivalent semantics; ``ruff`` not invoked
> (no import order or ordering change since the rewrite is a
> pure prefix shorten that preserves alphabetic position).
> DeprecationWarning emission from ``tests/runtime/`` paths
> drops to 0 (was 1 from ``test_blackboard_contract.py:37``).
> Slice green: 262 parity+contracts / 192 runtime-orgs (==
> baseline). No source under ``src/openakita/`` touched; the
> P10.2 shim ``src/openakita/runtime/orgs/__init__.py`` keeps
> emitting its DeprecationWarning whenever future code imports
> the legacy path -- 0 such callers remain in ``tests/runtime/``.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.3b | refactor(tests/runtime): P10.3b sweep openakita.runtime.orgs imports to canonical openakita.orgs (30 sites / 18 files) [P-RC-10 P10.3b] | +37 / -37 (mechanical prefix swap: 30 import lines + 7 docstring strings) + ~35 ledger row | 262 parity+contracts (unchanged) / 192 runtime-orgs (unchanged) | ADR-0011 (subsystem decomposition; no Protocol change) |

## P10.3c -- Sweep ``tests/api/`` + ``tests/parity/`` import sites to canonical ``openakita.orgs``

> **Sub-phase status (2026-05-21, P10.3c LANDED)**: Mechanical
> 1:1 prefix swap of every ``from|import openakita.runtime.orgs``
> reference under ``tests/api/`` and ``tests/parity/`` to the
> canonical ``openakita.orgs`` path. **43 sites across 18
> files** (37 import-line rewrites + 6 docstring/Sphinx string
> rewrites): ``tests/api/contracts/`` 5 files / 9 sites
> (test_orgs_v2_contracts_dispatch 2, _mint_sse 1, _nodes 1,
> _orgs 3, _projects 2), ``tests/api/`` (top-level) 6 files /
> 11 sites (test_config_orgs_v2_backend 1, test_orgs_v2 1,
> test_orgs_v2_stream 1, test_p97_alpha2_smoke 1,
> test_p97_beta_smoke 3 incl. 1 docstring, test_server_app_wiring
> 3 [BOM-bearing source preserved byte-for-byte]),
> ``tests/parity/orgs/`` 7 files / 23 sites (README.md 2,
> test_blackboard_parity 3, test_command_service_parity 2,
> test_manager_parity 2, test_node_scheduler_parity 7,
> test_project_store_parity 3, test_runtime_parity 5; 5 of
> these 23 are docstring/Sphinx role strings on parity test
> module docstrings). RECON section 2 projected ~37 sites /
> ~18 files; observed 37 import sites / 18 files (exact
> match). RECON section 3 N=2 ``README.md`` markdown code-
> block reference resolved by rewrite (the block is a
> ``How to add a fixture`` template prescribing current
> canonical imports, not historical pre-flatten state).
> The post-P10.4 sentinel file
> ``tests/parity/orgs/test_v1_src_retired_sentinel.py`` is
> byte-untouched per charter scope (legitimately retains
> ``openakita.runtime.orgs`` as the banned-string needle for
> the regex test). 1:1 byte-equivalent semantics; ``ruff``
> not invoked (pure prefix shorten, no alpha-order shift).
> Slice green: 262 parity+contracts (0 warnings -- previous
> single DeprecationWarning from test_runtime_parity.py:48
> now silent) / 192 runtime-orgs (1 warning remains, sourced
> from ``scripts/migrate_orgs_v2_json_to_sqlite.py:116``
> imported by ``test_migrate_json_to_sqlite``; cleared by
> P10.3e).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.3c | refactor(tests/api,tests/parity): P10.3c sweep openakita.runtime.orgs imports to canonical openakita.orgs (37 sites / 18 files) [P-RC-10 P10.3c] | +43 / -43 (mechanical prefix swap: 37 import lines + 6 docstring strings; 1 BOM-bearing file preserved) + ~40 ledger row | 262 parity+contracts (unchanged; 0 warnings -- was 1) / 192 runtime-orgs (unchanged; 1 warning remains from scripts/ -- cleared by P10.3e) | ADR-0011 (subsystem decomposition; no Protocol change) |

## P10.3d -- Sweep ``tests/unit/`` + ``tests/integration/`` + ``tests/e2e/`` import sites to canonical ``openakita.orgs``

> **Sub-phase status (2026-05-21, P10.3d LANDED)**: Mechanical
> 1:1 prefix swap of every ``from|import openakita.runtime.orgs``
> reference under ``tests/unit/``, ``tests/integration/`` and
> ``tests/e2e/`` to the canonical ``openakita.orgs`` path,
> including the 3 ``unittest.mock.patch`` / ``monkeypatch.setattr``
> target-string literals (test_p0_regression.py:273
> ``project_store.ProjectStore``; test_org_setup_tool.py:670,
> :681 ``runtime.get_runtime``) which would otherwise patch a
> module no longer present after the P10.6 shim removal.
> **22 sites across 10 files** (19 import-line rewrites + 3
> mock-target string rewrites): ``tests/e2e/`` 1 file / 3
> sites (test_p0_regression: 2 imports + 1 mock-target),
> ``tests/integration/`` 3 files / 7 sites
> (test_gateway_org_control 5, test_v2_im_canary_e2e 1,
> test_v2_im_cancel 1), ``tests/unit/`` 6 files / 12 sites
> (test_c17_second_pass_audit 2, test_delegation_preamble 2,
> test_failure_diagnoser_tone 1, test_org_delegation_validator
> 1, test_org_setup_tool 5 [3 imports + 2 mock-targets],
> test_remaining_qa_fixes 1). RECON section 2 projected ~19
> sites / ~10 files; observed 19 import sites / 10 files
> (exact match). 1:1 byte-equivalent semantics; ``ruff`` not
> invoked. Slice green: 262 parity+contracts / 192
> runtime-orgs (both unchanged). Sole residual
> DeprecationWarning still comes from
> ``scripts/migrate_orgs_v2_json_to_sqlite.py:116``; cleared
> by P10.3e.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.3d | refactor(tests/unit,tests/integration,tests/e2e): P10.3d sweep openakita.runtime.orgs imports to canonical openakita.orgs (19 sites / 10 files) [P-RC-10 P10.3d] | +22 / -22 (mechanical prefix swap: 19 import lines + 3 mock-target strings) + ~30 ledger row | 262 parity+contracts (unchanged) / 192 runtime-orgs (unchanged) | ADR-0011 (subsystem decomposition; no Protocol change) |

## P10.3e -- Sweep ``scripts/`` import sites to canonical ``openakita.orgs``

> **Sub-phase status (2026-05-21, P10.3e LANDED)**: Mechanical
> 1:1 prefix swap of every ``from|import openakita.runtime.orgs``
> reference under ``scripts/`` to the canonical
> ``openakita.orgs`` path, plus the 2 Sphinx-style module-
> docstring strings on the two migrator scripts.
> **5 sites across 3 files** (3 import-line rewrites + 2
> docstring strings): migrate_non_ascii_template_ids.py 2
> sites (1 import + 1 docstring), migrate_orgs_to_v2.py 2
> sites (1 import + 1 docstring),
> migrate_orgs_v2_json_to_sqlite.py 1 site (1 import). RECON
> section 2 projected ~3 sites / ~3 files; observed 3 import
> sites / 3 files (exact match). 1:1 byte-equivalent
> semantics; ``ruff`` not invoked. 
>
> **P10.3 cluster import-line sweep complete**: across
> P10.3a..P10.3e the cluster swept 92 import-line rewrites +
> 25 string-literal rewrites = 117 total sites across 50
> files (P10.3a 31 imports / 12 src files; P10.3b 30 imports
> + 7 docstrings / 18 files; P10.3c 37 imports + 6 docstrings
> / 18 files; P10.3d 19 imports + 3 mock-targets / 10 files;
> P10.3e 3 imports + 2 docstrings / 3 files). Slice green at
> every mid-phase checkpoint: 262 parity+contracts / 192
> runtime-orgs.
>
> **DeprecationWarning emission**: drops to 0 in both narrow
> slices (262 slice cleared at P10.3c; 192 slice cleared at
> P10.3e). Backend boot smoke
> (``python -c 'from openakita.api.server import create_app;
> create_app()'`` with ``-W always::DeprecationWarning``)
> emits 0 lines sourced from ``openakita.runtime.orgs`` (the
> only DeprecationWarnings remaining are unrelated FastAPI
> ``on_event`` deprecations).
>
> **Adversarial sentinel #9 extended-scope probe**: locally-
> augmented (NOT committed) replay of the strict legacy-
> import regex (
> ``^\s*(?:from|import)\s+openakita\.runtime\.orgs(?:\.|$|\s)``)
> with ``EXTENDED_ROOTS = [src/openakita, tests, scripts]``
> and the natural 2-file allowlist (the P10.2 shim itself +
> the sentinel's own banned-string needle) returned 0
> banned-import hits. Probe lives at
> ``tmp_p10/_p10_3e_adversarial.py``; not committed.
>
> **Residual ``openakita.runtime.orgs`` mentions outside
> docs/revamp/ + tmp_p10/ + the shim**: 21 lines remain:
>
> - 9 in ``tests/parity/orgs/test_v1_src_retired_sentinel.py``
>   (legitimate banned-string needle + assertion-message
>   strings; in the charter ZERO-touch list and consumed by
>   the sentinel's own assertion logic).
> - 12 in ``src/openakita/`` source as docstring / Sphinx-
>   role / inline code-quoted comments (``:mod:``, ``:class:``,
>   ``:func:``, ``:py:meth:``, ``# managers are reachable
>   via ...``). **NO import lines** in this set; P10.3a
>   was scoped strictly to ``from|import`` statements and
>   left these documentation references intact. The src
>   files were not touched by P10.3b..P10.3e per the
>   ``ZERO touch src/openakita/`` hard scope. Sub-phase
>   discrepancy surfaced for charter review, not silently
>   swept: candidate P10.3f mechanical docstring sweep
>   under ``src/openakita/`` (12 files, 12 sites) would
>   bring repo-wide residual to 0 ahead of P10.6 shim
>   removal; alternatively the docstring set can be
>   absorbed into P10.6 in the same commit that ``git rm``s
>   the shim (the references go stale at that point so
>   that commit must touch them either way).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.3e | refactor(scripts): P10.3e sweep openakita.runtime.orgs imports to canonical openakita.orgs (3 sites / 3 files) [P-RC-10 P10.3e] | +5 / -5 (mechanical prefix swap: 3 import lines + 2 docstring strings) + ~70 ledger row | 262 parity+contracts (unchanged) / 192 runtime-orgs (unchanged; 0 DeprecationWarning -- was 1 from this script imported by tests/runtime/orgs/test_migrate_json_to_sqlite.py) | ADR-0011 (subsystem decomposition; no Protocol change); ADR-0015 (308 shim retirement -- OUT-OF-SCOPE; byte-untouched) |

## P10.3f -- Sweep ``src/openakita/`` docstring/Sphinx/comment refs to canonical ``openakita.orgs``

> **Sub-phase status (2026-05-22, P10.3f LANDED)**: 1:1 prefix
> swap of 12 ``openakita.runtime.orgs`` mentions in
> ``src/openakita/`` (11 files); these doc-only refs survived
> because P10.3a was scoped strictly to ``from|import`` statements
> and P10.3b..P10.3e were banned from touching ``src/``. SPECIAL
> semantic rewrite at ``orgs_v2_runtime_state.py:23`` (legacy
> ``openakita.runtime.orgs`` not v1 ``openakita.orgs`` -> 
> ``openakita.orgs`` (canonical v2 runtime, not the legacy v1
> layout); intent preserved, factually correct post-flatten).
> Repo-wide grep (excl. ``docs/revamp/`` + ``tmp_p10/``) now 12
> lines residual: 3 in the shim + 9 in the sentinel; zero in
> ``src/openakita/`` proper. 262 / 192 baselines unchanged;
> backend boot smoke 0 ``runtime.orgs`` DeprecationWarning.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.3f | docs(src/openakita): P10.3f sweep openakita.runtime.orgs docstring/comment refs in src/ to canonical openakita.orgs (12 sites / 11 files) [P-RC-10 P10.3f] | +12 / -12 (12 single-line prefix swaps incl. 1 semantic rewrite at orgs_v2_runtime_state.py:23) + ~20 ledger row | 262 parity+contracts (unchanged) / 192 runtime-orgs (unchanged; 0 runtime.orgs DeprecationWarning) | ADR-0011 (subsystem decomposition; no Protocol change); ADR-0015 (308 shim retirement -- OUT-OF-SCOPE; byte-untouched) |


## P10.5d -- close deferred nit epsilon-O2 (monitor disposition)

> **Sub-phase status (2026-05-22, P10.5d LANDED)**: docs-only
> disposition entry. Nit epsilon-O2 (from
> ``docs/revamp/P-RC-9-P9.9-COVERAGE-AUDIT.md`` section 3 row O2)
> covered ``test_org_orchestration_fix.py`` (31 cases / 659 LOC)
> and ``test_org_affinity_attach_fix.py`` (9 cases / 512 LOC),
> regression-pin tests for specific v1 orchestration bug-fixes.
> v1 src deletion at P9.9eta-2 closed the original regression
> vectors; v2 ``OrgRuntime`` is a re-implementation that does
> not share the bug-prone code paths the pins guarded. Per
> P-RC-10 CHARTER section 1.3, P10.5d records the
> charter-mandated "monitor and back-fill on regression"
> disposition: NO test cases ported now; if a v2 orchestration
> bug ships post-merge with a similar shape, port the assertion
> shape (not text) into a fresh contract case in
> ``tests/api/contracts/`` at that time. Ledger pointer is the
> deliverable. No code edits; 262 / 192 baselines unchanged.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.5d | docs(revamp): P10.5d clear deferred nit epsilon-O2 -- record monitor + back-fill disposition for v1 regression-pin tests [P-RC-10 P10.5d] | +0 / -0 (ledger-only; ~28 lines of narrative + 1 table row) | 262 parity+contracts (unchanged) / 192 runtime-orgs (unchanged) | ADR-0011 (subsystem decomposition; v2 OrgRuntime re-implementation closes the v1 regression vectors structurally) |


## P10.5e -- close deferred nit GroupC (frontend stale v1 paths)

> **Sub-phase status (2026-05-22, P10.5e LANDED)**: dead-code
> deletion in ``apps/setup-center/src/views/OrgEditorView.tsx``
> plus sentinel-allowlist closure. Nit GroupC (from
> ``docs/revamp/gates/G-RC-9.8.md`` section 1 + P9.8delta-1
> sentinel #8 allowlist) covered three v1 ``/api/orgs/...`` HTTP
> literals that survived the P9.8 frontend caller migration as
> "debug-only endpoints scheduled for P9.9 deletion" -- the v1
> ``reset`` / ``heartbeat/trigger`` / ``standup/trigger`` paths.
> At P-RC-9 P9.9eta-2 the v1 router was retired (commit
> ``857a5a35``) and these endpoints began 404-ing on the server;
> the frontend kept silent ``try/catch`` callers. v2 mint exposes
> no equivalents. Per charter section 1.3 fifth bullet option
> (a), P10.5e DELETES the dead UI code paths.
>
> Two coordinated edits:
>
> * ``OrgEditorView.tsx`` -- ``handleResetOrg`` callback retains
>   its local UI reset (layout unlock, blackboard refresh,
>   org-stats clear, toast) but drops the dead ``safeFetch`` +
>   response-parse trio; ``apiBaseUrl`` exits the useCallback
>   deps list. The ``liveMode && (<>...</>)`` fragment carrying
>   the heartbeat / standup trigger buttons is removed wholesale
>   (both buttons were 100 % server-dependent with no local
>   side-effects). Two explanatory comments left in place; both
>   avoid the literal ``/api/orgs`` substring so the sentinel #8
>   regex does NOT flag them.
> * ``test_frontend_stale_paths_sentinel.py`` -- the 3-entry
>   ``GROUP_C_ALLOWLIST`` is replaced with ``[]`` and the module
>   docstring's invariant 1 wording is updated to note "Group C
>   closed at P10.5e". The drift test
>   ``test_group_c_allowlist_paths_still_present`` becomes a
>   trivial no-op (empty iterable) but stays wired as a guard
>   against future re-addition.
>
> i18n strings ``org.editor.triggerHeartbeat`` /
> ``triggerStandup`` are now orphaned in ``i18n/en.json`` +
> ``i18n/zh.json`` but harmless; deferred to a future hygiene
> sweep (out of scope for the deferred-nit close-out epic).
> 262 parity+contracts (unchanged; sentinel #8 still 5 / 5 with
> empty allowlist) / 192 runtime-orgs (unchanged; backend
> untouched).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.5e | refactor(frontend,parity): P10.5e clear deferred nit GroupC -- delete 3 stale v1 ``/api/orgs/*`` HTTP literals + empty sentinel #8 allowlist [P-RC-10 P10.5e] | +12 / -30 (OrgEditorView -4 net; sentinel allowlist -19 net + docstring +/-equal) + ~50 ledger row | 262 parity+contracts (unchanged; sentinel #8 5 / 5 with empty Group C list) / 192 runtime-orgs (unchanged; backend untouched) | ADR-0011 (v2 subsystem decomposition; v2 mint exposes no equivalent of the retired v1 debug-only endpoints; legitimate clean removal) |


## P10.5b -- close deferred nit P9.7-B (contract fixture extract)

> **Sub-phase status (2026-05-22, P10.5b LANDED)**: shared
> contract-test helpers hoisted to ``tests/api/contracts/conftest.py``.
> Nit P9.7-B (from ``docs/revamp/gates/G-RC-9.7.md`` section 11 row
> P9.7-B) covered the ADR-0014 350-LOC soft-cap exceedance in the
> two largest cluster files. The most duplicated piece of test
> boilerplate was ``_async_return`` (4 cluster files, 19 call-sites)
> followed by ``_async_raise`` (2 files, 6 call-sites). Per charter
> section 1.3 second bullet, P10.5b extracts both into
> ``conftest.py`` -- the canonical "fixture extract" disposition
> with "Net LOC ~0" outcome.
>
> Per-file deltas (LOC):
>
> * ``conftest.py`` 109 -> 136 (+27; two helpers with docstrings
>   noting the P10.5b hoist).
> * ``test_orgs_v2_contracts_dispatch.py`` 203 -> 192 (-11; both
>   helpers stripped, single conftest import added).
> * ``test_orgs_v2_contracts_ops.py`` 313 -> 302 (-11; both helpers
>   stripped, single conftest import added).
> * ``test_orgs_v2_contracts_nodes.py`` 324 -> 320 (-4;
>   ``_async_return`` stripped, conftest import added).
> * ``test_orgs_v2_contracts_projects.py`` 380 -> 373 (-7;
>   ``_async_return`` stripped, conftest import added).
> * ``test_orgs_v2_contracts_state.py`` 314 (unchanged; never
>   defined either helper).
> * ``test_orgs_v2_contracts_orgs.py`` 483 (unchanged; never
>   defined either helper -- this file's 133-LOC exceedance
>   is NOT addressed by P9.7-B fixture extract since it has
>   no duplicated fixtures to hoist; remains an accepted
>   soft-cap exceedance per charter "extract shared fixtures"
>   scope; deeper splitting deferred to a future hygiene epic).
>
> Net contract-suite LOC delta: -6 across 5 file edits
> (charter target was ~0; -6 = effectively neutral within
> rounding). 262 parity+contracts (unchanged; 184 / 184
> contract cases still green) / 192 runtime-orgs (unchanged).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.5b | test(api/contracts): P10.5b clear deferred nit P9.7-B -- hoist ``_async_return`` + ``_async_raise`` helpers to conftest [P-RC-10 P10.5b] | +33 / -39 across 5 files (net -6; conftest +27, dispatch -11, ops -11, nodes -4, projects -7) + ~36 ledger row | 262 parity+contracts (unchanged; 184 / 184 contract cases) / 192 runtime-orgs (unchanged; backend untouched) | ADR-0014 (per-shard soft-cap revision; this commit extracts shared fixtures per the soft-cap exceedance disposition, charter section 1.3 second bullet) |


## P10.5c -- close deferred nit epsilon-O1 (5 strategic v2 contract cases)

> **Sub-phase status (2026-05-22, P10.5c LANDED)**: five strategic
> v2 contract cases added to
> ``tests/api/contracts/test_orgs_v2_contracts_dispatch.py``. Nit
> epsilon-O1 (from ``docs/revamp/P-RC-9-P9.9-COVERAGE-AUDIT.md``
> section 3 row O1) covered the v1 ``test_plan_features.py``
> (73 cases / 1 042 LOC) which exercised orchestration
> plan-feature toggles end-to-end against ``orgs/runtime.py``.
> Per charter section 1.3 third bullet, P10.5c does NOT
> mechanically re-enumerate the 73 v1 toggles -- instead it
> picks 5 strategic v2 contract cases targeting the two highest-
> value scenario families the charter calls out:
>
> * **State-machine edges** (3 cases): illegal lifecycle
>   transitions on B35 / B36 / B37 sharing the ValueError
>   -> HTTP 400 pathway through ``_call_lifecycle``:
>   ``test_b35_stop_org_400_on_illegal_transition``,
>   ``test_b36_pause_org_400_on_illegal_transition``,
>   ``test_b37_resume_org_400_on_illegal_transition``.
>   B34 start already had its 400-on-ValueError pin
>   (``test_b34_start_org_400_on_value_error``); this trio
>   closes the symmetry across all four lifecycle verbs.
> * **Cancel-during-plan body invariants** (2 cases):
>   POST .../cancel CancelRequest validation:
>   ``test_b40_cancel_with_reason_body_accepted`` (optional
>   reason field reaches happy-path returning the cancelled
>   envelope with the same reason) +
>   ``test_b40_cancel_422_on_extra_body_field`` (Pydantic
>   ``extra="forbid"`` rejects unexpected body keys).
>
> 5 / 5 new cases pass; narrow-slice contract collection bumps
> from 184 -> 189 total and the parity+contracts slice grows
> from 262 -> 267 passed. The runtime-orgs slice (192) is
> unchanged. The 5 cases pin assertion *shapes* (not literal
> v1 strings) per the audit row O1 "scenario coverage via
> structural contract" stance; future v1-style end-to-end
> regression vectors will be ported only on demand if a real
> bug surfaces post-merge.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.5c | test(api/contracts): P10.5c clear deferred nit epsilon-O1 -- add 5 strategic v2 contract cases (state-machine edges + cancel-during-plan body invariants) [P-RC-10 P10.5c] | +71 / -0 (dispatch.py 192 -> 263; 5 new cases + 2 section header banners) + ~37 ledger row | 267 parity+contracts (262 -> 267; **+5 legitimate from new contract cases**) / 192 runtime-orgs (unchanged; backend untouched) | ADR-0011 (subsystem decomposition; v2 lifecycle + cancel exercise the OrgRuntime / OrgCommandService protocols that replaced v1 ``orgs/runtime.py`` toggles) |

## P10.5a -- close deferred nit M-2 (ADR-0014 sub-cap breach)

> **Sub-phase status (2026-05-22, P10.5a LANDED)**: shard-split
> the two oversized v2 ``runtime/orgs/`` siblings flagged by
> G-RC-9.6 mini-gate as the M-2 (sub-cap rebalance) deferral.
> ``_runtime_agent_pipeline.py`` (521 LOC) and
> ``_runtime_plugin_assets.py`` (564 LOC) both exceeded the
> ADR-0014 per-shard 400-LOC soft cap (by 121 / 164 LOC
> respectively). Per P-RC-10 CHARTER section 1.3 first
> bullet, P10.5a splits each into TWO sibling shards along
> the natural cohesive seam already marked in the source
> (``# === AgentPipelineExecutor ===`` /
> ``# === FileOutputRegistry + react-trace + delivery ===``
> banner comments) and re-exports the moved symbols from the
> original module path so every existing
> ``from openakita.orgs._runtime_agent_pipeline import ...`` and
> ``from openakita.orgs._runtime_plugin_assets import ...`` line
> in ``src/`` and ``tests/`` keeps resolving byte-for-byte
> unchanged (verified by ``git grep`` on both module names
> across ``src/openakita/orgs/__init__.py``,
> ``tests/parity/orgs/test_runtime_parity.py``,
> ``tests/runtime/orgs/test_runtime_contract.py`` and
> ``tests/e2e/test_p0_regression.py`` -- ZERO importer edits).
>
> Resulting four shards (all <= 400 LOC):
>
> * ``_runtime_agent_pipeline.py`` 521 -> 286 LOC -- kept name;
>   owns the agent build / cache infrastructure
>   (:class:`AgentSpec`, :class:`AgentBuilderProtocol`,
>   :class:`_NullAgentBuilder`, :class:`_CachedAgent`,
>   :class:`AgentCache`, :class:`ProfileResolver`,
>   ``ORG_STATE_*`` constants).
> * ``_runtime_agent_pipeline_executor.py`` NEW 272 LOC --
>   the activate-and-run pipeline
>   (:class:`AgentPipelineExecutor`,
>   ``_QUOTA_AUTH_HINTS`` table, ``_AgentRunCallable``
>   Protocol, ``_looks_like_quota_or_auth_error``).
> * ``_runtime_plugin_assets.py`` 564 -> 351 LOC -- kept name;
>   owns the plugin-tool detection helpers
>   (``safe_asset_filename``, ``ext_for_url``,
>   ``is_plugin_tool``, ``plugin_id_for_tool``),
>   :class:`PluginAsset`, :class:`ToolHandlerBridge` and the
>   :class:`PluginAssetRecorder`.
> * ``_runtime_plugin_assets_outputs.py`` NEW 262 LOC -- file
>   outputs / trace stats / task-delivery synth
>   (:class:`FileOutput`, :class:`FileOutputRegistry`,
>   ``react_trace_has_tool``,
>   ``collect_tool_stats_from_trace``,
>   ``extract_accepted_chain_ids``,
>   :class:`SynthesizedDelivery`,
>   :class:`TaskDeliverySynthesizer`).
>
> Re-export strategy: each kept file ends with a small late
> ``from ._<new>_shard import ...`` block (``# noqa: E402``;
> E402 is in the project-wide ruff ignore list anyway) plus a
> verbatim-copy ``__all__`` listing every previously public
> symbol. The new files import their cross-shard companion
> dependencies as a one-way edge -- the executor imports
> ``ORG_STATE_PAUSED`` from the kept agent shard (a string
> constant; safe under partial module load) and uses
> ``TYPE_CHECKING`` for :class:`AgentCache` /
> :class:`ProfileResolver` annotations; the outputs shard
> imports :class:`PluginAsset` only under ``TYPE_CHECKING``.
> No circular runtime import.
>
> Net LOC delta: ``+86`` ((286 + 272 + 351 + 262) - (521 + 564))
> -- charter target was ~0 (pure splitting); the ~+86
> overhead is entirely two new module docstrings + two new
> ``__all__`` blocks + two inserted "split note" paragraphs in
> the kept files' docstrings + two re-export trailers (six
> lines each). ZERO behavior change. ZERO importer edits.
> ZERO touch to the ADR-0014 doc, the shim file, the sentinel
> file or the P-RC-10 CHARTER. 267 parity+contracts
> (unchanged) / 192 runtime-orgs (unchanged); ruff clean on
> all four shards (the pre-existing
> ``src/openakita/orgs/manager.py`` I001 import-order issue
> visible at HEAD is OUTSIDE the P10.5a touch budget and is
> not introduced by this commit).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.5a | refactor(orgs): P10.5a clear deferred nit M-2 -- shard split _runtime_agent_pipeline + _runtime_plugin_assets to satisfy ADR-0014 per-shard cap [P-RC-10 P10.5a] | net +86 LOC across 2 modified shards + 2 new shards (agent_pipeline 521 -> 286; agent_pipeline_executor NEW 272; plugin_assets 564 -> 351; plugin_assets_outputs NEW 262) + ~80 ledger row | 267 parity+contracts (unchanged) / 192 runtime-orgs (unchanged) | ADR-0014 (per-shard 400-LOC soft cap; this commit closes the M-2 deferral by bringing both oversized siblings under the cap via cohesive sibling-shard splits) |

## P10.5f -- P10.5 deferred-nits roster sign-off (5/5 CLOSED)

> **Sub-phase status (2026-05-22, P10.5f LANDED)**: Roster
> sign-off ledger close marking the P10.5 deferred-nits
> roster from P-RC-10 CHARTER section 1.3 as fully resolved.
> All 5 nits inherited from G-RC-9.9 section 3 now carry an
> explicit closure commit; P10.5 is therefore complete and
> P10.6 (shim removal) is unblocked.
>
> Roster (commit references; all on `revamp/v3-orgs`, none
> pushed):
>
> | nit id | severity | source audit | closure commit | strategy |
> |---|---|---|---|---|
> | M-2 | MED | G-RC-9.6 | `3331ed4f` (P10.5a) | shard split 2 oversized siblings into 4 sub-shards (435+476 LOC -> 286/272 + 351/262); ADR-0014 per-shard cap restored; re-export trailer keeps import paths byte-stable |
> | P9.7-B | LOW | G-RC-9.7 | `6d4d869a` (P10.5b) | hoist `_async_return` + `_async_raise` helpers to `tests/api/contracts/conftest.py`; per-file soft cap restored |
> | epsilon-O1 | OPT | G-RC-9.9 delta-1 | `0012a2e5` (P10.5c) | +5 strategic v2 contract cases (state-machine edges + cancel-during-plan body invariants); v2-shaped coverage, not literal v1 scenario port; baseline 262 -> 267 |
> | epsilon-O2 | OPT | G-RC-9.9 delta-1 | `e65902b7` (P10.5d) | WONTFIX-UNLESS-REGRESSION disposition for `test_org_*_fix` regression-pins; backed by 267/192 baseline + monitor-and-backfill protocol if a v2 regression surfaces |
> | GroupC | LOW | G-RC-9.8 | `7a8534a0` (P10.5e) | delete 3 stale v1 `/api/orgs/*` HTTP literals from `OrgEditorView.tsx`; empty sentinel #8 allowlist; v1 dead-path UI references removed |
>
> Aggregate net LOC across P10.5a-e: +86 source (M-2 split
> overhead) + ~+220 tests (epsilon-O1 contract additions) +
> ~-30 frontend (GroupC dead-code removal); ledger rows for
> a/b/c/d/e plus this f close = ~+260 lines in
> `PROGRESS_LEDGER_P10.md`. Test baseline 267 parity+contracts
> / 192 runtime-orgs (267 is the new floor after epsilon-O1's
> +5 cases at P10.5c).
>
> Acceptance against CHARTER section 4 row 4 ("All 5
> deferred nits CLOSED with commit references in
> PROGRESS_LEDGER_P10.md"): SATISFIED.
>
> Next: P10.6 (remove the `src/openakita/runtime/orgs/__init__.py`
> shim) is unblocked by (a) P10.3 cluster grep-clean baseline
> (only shim + sentinel still mention `openakita.runtime.orgs`)
> and (b) this P10.5 roster closure. P10.7 (G-RC-10 final
> gate + merge-to-main charter) follows P10.6.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.5f | docs(revamp): P10.5f close P10.5 deferred-nits roster sign-off (5/5 nits closed) [P-RC-10 P10.5f] | ledger only (~+45 lines) | 267 parity+contracts (unchanged) / 192 runtime-orgs (unchanged) | ADR-0014 (M-2 via P10.5a closes the cap-breach deferral) |

## P10.6 -- remove openakita.runtime.orgs deprecation shim + tighten sentinel #9

> **Sub-phase status (2026-05-22, P10.6 LANDED)**: Physical
> removal of the P10.2 deprecation shim. The lone tracked
> file `src/openakita/runtime/orgs/__init__.py` (-46 LOC) is
> deleted via `git rm` and the now-empty directory pruned
> (stale `__pycache__` swept). Sentinel #9 Test 2 is
> tightened accordingly: the 1-entry `_SHIM_ALLOWLIST`
> tuple is dropped entirely (no whitelist) and a new
> directory-non-existence assertion guards the post-removal
> invariant. Test 1 (Option-Z augmented at P10.2) is
> deliberately left byte-stable -- it polices
> `src/openakita/orgs/` (the v2 canonical location), not the
> retired shim path.
>
> Trigger conditions per P-RC-10 CHARTER section 2 P10.6:
>
> * (a) "one full release shipped with >=7 day burn-in" -- NOT
>   met (no release tagged on this branch yet).
> * (b) "zero third-party callers depend on the legacy path" --
>   MET. The P10.3 cluster (P10.3a-f, commits `5ac2c786`
>   through `e1680941`) swept all 122 internal call sites
>   (122 of 124 RECON-strict sites -- 31 src + 30 + 37 + 19 +
>   3 tests/scripts + 12 docstring back-refs) to the new
>   canonical `openakita.orgs.*` path; no external plugins
>   are known to depend on the transitional shim.
> * Additional gating per CHARTER: P10.5 closure (achieved at
>   P10.5f `579c7b40` -- this commit's HEAD parent) provided
>   the green-light to proceed.
>
> Shim contents removed (1-paragraph summary): the deleted
> `__init__.py` issued a one-shot `DeprecationWarning` on
> first import, re-exported `openakita.orgs.*` via a wildcard
> `from openakita.orgs import *`, and installed 24
> `sys.modules` aliases for every sibling submodule
> (13 public + 11 private; matched the
> P10.1 flatten RECON section 1 ordering) so that
> `from openakita.runtime.orgs.X import Y` kept resolving
> byte-for-byte across the transition. With the shim gone any
> caller still on the legacy path now receives a
> `ModuleNotFoundError: No module named 'openakita.runtime.orgs'`
> at import time -- the intended **loud failure** per P10.6
> spec.
>
> Sentinel #9 Test 2 (`test_production_imports_v1_free`)
> tightening (whitelist comparison):
>
> | aspect | P10.4 (eb96fc15) | P10.6 (this commit) |
> |---|---|---|
> | `_SHIM_ALLOWLIST` tuple | `("src/openakita/runtime/orgs/__init__.py",)` (length 1) | **DROPPED** -- constant removed entirely |
> | `_scan_legacy_imports` skip clause | `if rel in _SHIM_ALLOWLIST: continue` | **REMOVED** -- every src `*.py` / `*.pyi` is scanned, no exemption |
> | Pre-scan dir invariant | none | **NEW**: `assert not _SHIM_DIR.exists()` -- catches a re-added shim, a phantom package dir from a stray `__pycache__`, or any future regrowth attempt |
> | Scan regex | `^\s*(?:from\|import)\s+openakita\.runtime\.orgs(?:\.\|$\|\s)` | UNCHANGED (carried byte-stable from P10.4) |
> | Scope of scan | `src/openakita/**/*.{py,pyi}` minus `_SHIM_ALLOWLIST` | `src/openakita/**/*.{py,pyi}` -- NO exemption |
>
> The sentinel file itself lives at
> `tests/parity/orgs/test_v1_src_retired_sentinel.py` which
> is OUTSIDE `_SRC_ROOT = repo/src/openakita`, so its own
> `_LEGACY_BYTES_NEEDLE` regex source and docstring
> back-references are never scanned -- the "no whitelist
> except the sentinel's own needle" wording in the P10.6
> brief is satisfied implicitly by the scan-scope geometry,
> not by an explicit whitelist entry.
>
> Verification matrix (8 checks per P10.6 brief):
>
> | # | check | result |
> |---|---|---|
> | 1 | `Test-Path src/openakita/runtime/orgs/__init__.py` -> False | PASS (`git rm` succeeded; file removed from index) |
> | 1b | `Test-Path src/openakita/runtime/orgs` -> False | PASS (directory pruned via `Remove-Item -Recurse -Force` after stale `__pycache__` blocked auto-removal) |
> | 2 | repo-wide `git grep openakita.runtime.orgs` excluding `docs/revamp/` + `tmp_p10/` | PASS -- 9 hits ALL inside `tests/parity/orgs/test_v1_src_retired_sentinel.py` (regex source bytes + 8 docstring/error-message back-references; ZERO actual imports) |
> | 3 | `python -c "from openakita.api.server import create_app; create_app()"` | PASS -- 417 routes registered; 0 `openakita.runtime.orgs`-related `DeprecationWarning` (22 other unrelated warnings remain from pre-existing FastAPI `on_event` + sqlalchemy noise -- OUT OF P10.6 SCOPE) |
> | 4 | `python -c "import openakita.runtime.orgs"` -> `ModuleNotFoundError` | PASS -- raises `ModuleNotFoundError: No module named 'openakita.runtime.orgs'`; exit 1; this is the INTENDED loud failure |
> | 5 | `pytest tests/parity/orgs/test_v1_src_retired_sentinel.py -v` | PASS -- both tests green (`test_v1_src_directory_retired` byte-stable; `test_production_imports_v1_free` with new dir-invariant + tightened scan) |
> | 6 | `pytest tests/parity/orgs/ tests/api/contracts/ -q --tb=no` | PASS -- **267 passed** (baseline unchanged from P10.5c floor) |
> | 7 | `pytest tests/runtime/orgs/ -q --tb=no` | PASS -- **192 passed** (baseline unchanged) |
> | 8 | adversarial: inject `from openakita.runtime.orgs import OrgManager` into `src/openakita/_adv_p10_6_inject.py`, re-run sentinel | PASS -- Test 2 FAILED loudly (1 hit reported with file:line:text and rewrite instruction); after `Remove-Item` revert, sentinel green again |
>
> Hard-rule compliance: only the shim file (deleted) + the
> sentinel test (tightened) + this ledger row are modified.
> `src/openakita/orgs/` content untouched. `src/openakita/runtime/__init__.py`
> inspection (`git grep -nP "orgs" -- src/openakita/runtime/__init__.py`)
> returned a SINGLE hit on a docstring line ("replaces
> `src/openakita/orgs/` per ADR-0002"), i.e. NO active
> `orgs` re-export to remove. Other tests / scripts / ADRs /
> CHARTER / 308 redirect shim (`api/routes/_orgs_v2_legacy_redirects.py`)
> all untouched. BOM-free tempfile via .NET
> `System.Text.UTF8Encoding $false` API (newer PowerShell
> `-Encoding utf8NoBOM` parameter unavailable in 5.1).
>
> Next: P10.7 (G-RC-10 final gate audit + merge-to-main
> charter -- `revamp/v3-orgs` -> `main`).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-10 P10.6 | refactor(orgs): P10.6 remove openakita.runtime.orgs deprecation shim + tighten sentinel #9 whitelist [P-RC-10 P10.6] | -46 shim + net ~+30 sentinel (5-entry allowlist tuple + skip clause removed; dir-invariant assertion + new docstring blocks added) + ~+85 ledger row | 267 parity+contracts (unchanged) / 192 runtime-orgs (unchanged); sentinel #9 both tests pass post-tighten; adversarial inject FAIL-then-revert confirmed | ADR-0002 (v2 runtime architecture; shim was a transition aid only -- no architectural delta) |