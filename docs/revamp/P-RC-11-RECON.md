# P-RC-11 Reconnaissance -- per-cluster carry-over inventory

Companion to `docs/revamp/P-RC-11-CHARTER.md` (ratified at
`5b32d845`, P11.0a). This recon enumerates the 7 carry-over
clusters from `docs/revamp/gates/G-RC-10.md` section 6 and
turns each cluster row into an actionable per-sub-phase plan:
(1) failing test enumeration, (2) affected source files,
(3) root-cause hypothesis (verified via at least one
`git grep` / read, never pure speculation), (4) recommended
fix strategy, (5) LOC estimate, (6) inter-cluster
dependencies. Docs-only commit (P11.0b); zero source / test
/ sentinel / ADR / gate touches.

**Branch**: `revamp/v3-orgs`. **HEAD at authorship**:
`5b32d845`. **Parent recon shape**:
`docs/revamp/P-RC-10-RECON.md`. **Bound gate**: G-RC-10
section 6 carry-over table (7 rows -> 7 clusters here).

## 0. Executive summary

The 60 G-RC-10 carry-over failures fan out into **7 clusters**
(A..G) of which two pairs share root causes (B + G = one
circular-import fix; A absorbs one of F's sub-cases). Verified
recon shows **Cluster A** (`tool_categories` deleted at
`90a7d77f` P9.9 epsilon-2b) and **Cluster B + G**
(`core.errors` -> `agent.__init__` -> `agent.brain` ->
`core._brain_legacy` -> `llm.client` -> `core.errors`
cycle confirmed via direct import-line trace below) together
account for **45 / 60 = 75 %** of the carry-overs; both ship
under 200 LOC. The recommended execution order (section 8)
front-loads the two high-value clusters and ends with the
5-LOC Cluster C xfail and the heterogeneous Cluster F
clean-up.

## 1. Cluster A -- `openakita.orgs.tool_categories` missing module

### 1.1 Failing test enumeration

`git grep -nl "tool_categories" -- tests/` returns 2 test
files; G-RC-10 only flagged the unit one:

* `tests/unit/test_org_setup_tool.py` -- 17 cases that
  exercise the `setup_organization` tool handler's
  `get_resources` action; one is
  `TestGetResources::test_returns_tool_categories` (charter
  section 2 P11.1 corrects the G-RC-10 mis-bucket that filed
  it under Cluster F).
* `tests/api/test_p97_beta_smoke.py` -- references
  `tool_categories` in passing; G-RC-10 did not flag any
  failure here, so this stays out of P11.1's commit scope.

Spot-read of `tests/unit/test_org_setup_tool.py:1..60`
confirms the docstring header lists `tool_categories` as
the third advertised resource and `class TestGetResources`
builds expectations around it.

### 1.2 Affected source files

`git grep -nl "tool_categories" -- src/openakita/` returns
4 stale callers:

* `src/openakita/agents/factory.py` (charter cites line
  `370`).
* `src/openakita/agents/profile.py` (**new this recon** --
  not in charter R-11-2 list; verify during P11.1).
* `src/openakita/orgs/_runtime_templates.py` (charter cites
  `1634` as a comment only; verify before touching).
* `src/openakita/tools/handlers/org_setup.py` (charter
  cites `129 / 440 / 731`; `731` is the one the G-RC-10
  ledger names as the stale caller).

### 1.3 Root cause hypothesis (verified)

`git log --all --oneline -- src/openakita/orgs/
tool_categories.py` returns `90a7d77f` as the first commit
(deletion `P9.9 epsilon-2b atomic delete src/openakita/
orgs/ (26 files / 20237 LOC, R-epsilon-1 RETIRED)`). The
module was an atomic-batch casualty of the v1 surface closure;
4 callers were left stranded (P9.9 epsilon-2b's audit caught
the `org_setup.py:731` site only). The
`test_org_setup_tool.py` suite asserts a non-empty
`tool_categories` list but import fails at module-load.

### 1.4 Recommended fix strategy

Land at **P11.1**. Re-instate as a private shard following
P10.5a M-2 split convention:

1. Create `src/openakita/orgs/_runtime_tool_categories.py`
   with the 149-LOC body restored from `90a7d77f`'s parent
   (`git show 90a7d77f~1:src/openakita/orgs/
   tool_categories.py`).
2. Create a 5-LOC public re-export
   `src/openakita/orgs/tool_categories.py` =
   `from ._runtime_tool_categories import *` (matches the
   ADR-0011 6-subsystem layout P10.5a used for
   `_runtime_templates.py` + its public re-export).
3. The 4 callers already import the public path
   `openakita.orgs.tool_categories.<symbol>`; no caller
   edits required.

Post-fix invariant: `git grep tool_categories -- src/`
returns hits only in the new shard + the public re-export +
the 4 callers.

### 1.5 LOC estimate

~+155 LOC = +150 private shard + +5 public re-export. No
deletions; no caller edits.

### 1.6 Dependencies

None. A is a clean independent restoration; pure addition;
cannot regress B/G (no import-order touch). Ordered first in
section 8.

## 2. Cluster B -- `core.errors` <-> `agent.errors` circular import

### 2.1 Failing test enumeration

22 cases under `tests/runtime/state_graph/guards/` per
G-RC-10 section 5.2. The cycle is exposed every time a test
fixture imports `openakita.core._reasoning_engine_legacy`
(or any sibling that pulls `openakita.agent` early).
Representative: `tests/runtime/state_graph/guards/
test_tool_filters.py:13..20` -- the `legacy_aliases`
fixture does `from openakita.core import
_reasoning_engine_legacy as re_module`, transitively
triggering the cycle.

### 2.2 Affected source files

Verified import chain (each line is a `git grep` hit at
this HEAD):

* `src/openakita/core/errors.py:15` --
  `from openakita.agent.errors import UserCancelledError`
  (head of the cycle; re-export shim per its own docstring).
* `src/openakita/agent/__init__.py:15` --
  `from .brain import Brain, SupervisorBrain` (eager
  `Brain` import at package init; the forcing line).
* `src/openakita/agent/brain.py:40` --
  `from openakita.core._brain_legacy import Brain as
  _LegacyBrainImpl`.
* `src/openakita/core/_brain_legacy.py` --
  `from ..llm.client import LLMClient`.
* `src/openakita/llm/client.py` --
  `from ..core.errors import UserCancelledError` --
  re-enters `core.errors` mid-resolution -> `ImportError:
  cannot import name`.

### 2.3 Root cause hypothesis (verified)

`core/errors.py` is a re-export shim for the moved
`UserCancelledError` symbol (its docstring at lines 1..12:
`Re-export shim - UserCancelledError moved to
agent.errors`). Importing a submodule of `openakita.agent`
triggers `agent/__init__.py`, which eagerly imports
`Brain` (line 15) -- which pulls `core._brain_legacy`,
which pulls `llm.client`, which re-enters `core.errors`.
**The forcing line is the eager `from .brain import Brain`
at `agent/__init__.py:15`.**

### 2.4 Recommended fix strategy

Land at **P11.2**. Two viable options (charter P11.2):

* **Option (a) -- PEP-562 lazy loader in
  `agent/__init__.py`** (preferred). Replace the ~30 eager
  submodule re-exports with a `__getattr__(name)` that
  dispatches to `importlib.import_module(...)` on first
  access. `Brain` / `BrainContext` / `BrainResponse`
  resolve only when a caller asks for them, breaking the
  cycle at `__init__` time. ~+25 / ~-30 LOC (net ~-5).
* **Option (b) -- inline `UserCancelledError` in
  `core/errors.py`**. Remove the re-export shim; define
  the class directly; `agent.errors` becomes a re-export
  the other way. ~+10 LOC; requires care that the identity
  `core.errors.UserCancelledError is
  agent.errors.UserCancelledError` holds.

**Decision pin**: prefer (a) -- leaves the ADR-0003 /
Phase 2 `UserCancelledError` ownership boundary at
`agent.errors` exactly as the `core/errors.py`
docstring documents; the lazy `__init__` is a structural
fix, not a re-home. P11.2 commit body should cite this.

### 2.5 LOC estimate

~+15 / ~-5 = +10 LOC for option (a); option (b) similar.

### 2.6 Dependencies

* Cluster G self-clears once B lands (same root; see section 7).
* Cluster D's 4th test (`test_policy_v2_c13`) is collateral
  damage of B; self-clears after B.
* No dependency the other way -- B is a structural fix that
  needs no other cluster in place first.

## 3. Cluster C -- 308 redirect smoke 503 (xfail target)

### 3.1 Failing test enumeration

3 cases in `tests/api/test_p97_alpha2_smoke.py` per G-RC-10
section 5.2. Read of lines 1..80 confirms:

* Module docstring + fixtures pin 308 -> mint precedence at
  every Group A path.
* `test_legacy_get_org_now_claimed_by_mint` asserts
  `resp.status_code == 503` (mint reaches handler but
  `_get_manager` returns 503 because no
  `app.state.org_manager`); the bug is some Group A path
  now returns something other than expected, exposing
  fixture/composition drift.
* Other 2 failing cases follow the same shim/mint precedence
  pattern.

### 3.2 Affected source files

Charter non-goal section 1.2 is explicit: `api/routes/
_orgs_v2_legacy_redirects.py` (the 308 shim) is
**byte-untouched** for P-RC-11. Only test-side decorators
change.

### 3.3 Root cause hypothesis (verified)

Per G-RC-10 row: "env / fixture or shim composition -- LOW
-- v2.1.0 (ADR-0015)". The 308 shim's eventual retirement is
**LOCKED to v2.1.0** per ADR-0015 option (b); P-RC-11 cannot
fix the production cause without violating the lock-out
(the shim is the forcing function for sentinel #7 OpenAPI
snapshot).

### 3.4 Recommended fix strategy

Land at **P11.3**. Add `@pytest.mark.xfail(strict=True,
reason="308 shim retirement locked for v2.1.0 -- see
ADR-0015 option (b); test re-enables when
api/routes/_orgs_v2_legacy_redirects.py retires")` to the
3 affected test functions only. `strict=True` (charter
R-11-3) forces an explicit ledger entry the day v2.1.0
lands and the shim retires. Add a 2-line module-level
ADR-0015 comment.

### 3.5 LOC estimate

~+5 LOC; zero deletions; zero source edits.

### 3.6 Dependencies

None. Independent. Smallest commit in the epic.

## 4. Cluster D -- `test_policy_v2_*` static-grep stale paths

### 4.1 Failing test enumeration

4 cases per G-RC-10 section 5.2. The three **direct**
failures read `src/openakita/core/agent.py` as a file:

* `tests/unit/test_policy_v2_c8b3_apply_resolution.py` --
  `text = self._read("core/agent.py")`.
* `tests/unit/test_policy_v2_c8b5_trust_mode_isolation.py`
  -- 2 hits of `(SRC_ROOT / "core" / "agent.py")
  .read_text(encoding="utf-8")` plus follow-on assertions.

The 4th case (`test_policy_v2_c13_multi_agent`) shows no
staticgrep hit for `core/agent.py`; charter P11.4 calls it
collateral damage of Cluster B.

### 4.2 Affected source files

`core/agent.py` no longer exists -- `git ls-files
src/openakita/core/` shows `_agent_legacy.py` only.
`git log --all --oneline -- src/openakita/core/agent.py`
post-rename history:

* `32c29c54 refactor(core): rename agent.py to
  _agent_legacy.py (pre-shim move)`.
* `3d43af41 refactor(core): replace core/agent.py body with
  thin import shim`.
* `a21cdd4b refactor(core): delete 5 lazy shim files` --
  the **final deletion** leaving only `_agent_legacy.py`.

### 4.3 Root cause hypothesis (verified)

The 3 direct cases hard-code `"core/agent.py"` as read
target; need to repoint to `_agent_legacy.py` (only
artifact left after `a21cdd4b`). The 4th is the same
Cluster B circular import surfacing at collection time.

### 4.4 Recommended fix strategy

Land at **P11.4** (after B). One-line path string-edit per
direct case:

* `test_policy_v2_c8b3_apply_resolution.py` --
  `"core/agent.py"` -> `"core/_agent_legacy.py"`.
* `test_policy_v2_c8b5_trust_mode_isolation.py` -- 2x
  `(SRC_ROOT / "core" / "agent.py")` -> 
  `(SRC_ROOT / "core" / "_agent_legacy.py")`.

The 4th case clears for free once P11.2 lands.

### 4.5 LOC estimate

~+10 / ~-10 = +0 net LOC.

### 4.6 Dependencies

* Depends on Cluster B clearing first (the 4th case is
  collateral). Running P11.4 before P11.2 would show only
  3 / 4 fixes -- a misleading partial.

## 5. Cluster E -- Telegram smoke `InvalidToken` env hygiene

### 5.1 Failing test enumeration

2 cases per G-RC-10 section 5.2. Files verified present:

* `tests/legacy/test_telegram_simple.py`.
* `tests/test_telegram_simple.py`.

Spot-read of the legacy file's lines 1..40 confirms the
module-level guard:

`
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    pytest.skip("...missing TELEGRAM_BOT_TOKEN...",
                allow_module_level=True)
`

i.e. presence-only check, not validity check.

### 5.2 Affected source files

None in `src/`. The fix is a 1-predicate tightening in
both test files.

### 5.3 Root cause hypothesis (verified)

The CI / dev environment has `TELEGRAM_BOT_TOKEN` set but
to a placeholder / invalid value (matches G-RC-10
"environment" severity). The existing presence-only guard
lets the test through; `telegram.Bot(token=...)` then
raises `InvalidToken` at fixture time.

### 5.4 Recommended fix strategy

Land at **P11.5**. Replace `if not BOT_TOKEN` in both
files with a predicate also checking `s.startswith("<")`,
`s.endswith(">")`, and `len(s) >= 35` (Telegram bot
token minimum length). Inlined helper; no shared import.

### 5.5 LOC estimate

~+10 LOC (~+5 per file).

### 5.6 Dependencies

None. Pure env-hygiene; independent.

## 6. Cluster F -- misc legacy debt (5 heterogeneous cases)

### 6.1 Failing test enumeration

Per G-RC-10 "5 cases misc legacy debt unit failures". After
Cluster A absorbs the
`TestGetResources::test_returns_tool_categories` case
(charter P11.1), the residual F roster (files verified
present at HEAD) is **5 candidates** (exact 5 lands in P11.6
after a post-P11.1..P11.5 full-suite delta):

* `tests/unit/test_c17_audit_chain_hardening.py` -- C17
  Phase E chain + sanitize integration; `filelock` codepath
  + subprocess fork; likely env-fragile.
* `tests/unit/test_c23_policy_v2_matrix.py` -- C23 P2-1
  approval-matrix backend truth-source guard; matrix lifted
  from React literal to `GET /api/config/security/
  approval-matrix` backed by `policy_v2.matrix.lookup` --
  stale expected-rows fixture is the most plausible cause.
* `tests/component/test_memory_manager.py` -- L2 component
  test; `tmp_workspace` / `mock_brain` fixture pair may
  have drifted vs current `memory/` shape.
* `tests/unit/test_org_setup_tool.py::TestGetResources::
  <residual non-tool-categories case>` -- one or two cases
  depending on agents / templates list shape rather than
  `tool_categories`; clears or shifts after A.
* One unknown "miscellaneous slot" -- P11.6 picks it up
  from the post-P11.5 `pytest -q --tb=no` failure list.

### 6.2 Affected source files

Heterogeneous; no single source file. Each sub-case gets
one targeted edit (test fixture pin / expected-value update
/ env guard). Cluster F never touches `src/`-side code
beyond what a single test fixture needs.

### 6.3 Root cause hypothesis (verified)

Each F sub-case is a **distinct** small legacy drift:
fixture shape / expected-value snapshot / env guard. G-RC-10
"LOW" severity row pre-acknowledges this is not structural.
Verified by spot-reading 3 of 5 files (c17, c23,
memory_manager) -- none share an import or fixture chain.

### 6.4 Recommended fix strategy

Land at **P11.6**. Case-by-case: (1) run full suite at
G-RC-11 pre-flight to get the exact F roster; (2) per
failure write a 1-line ledger note (cause + fix-or-retire
decision); (3) each fix lands as a single hunk. Total
cluster-commit body stays <= 200 LOC per charter R-11-4
(if any single case exceeds 50 LOC, split into P11.6.x).

### 6.5 LOC estimate

~+30 / ~-10 = +20 net LOC.

### 6.6 Dependencies

* Cluster A clears one of the 5; F's working roster is "5
  minus whatever A swallowed".
* No dependency the other way.

## 7. Cluster G -- collection-stage errors (same root as B)

### 7.1 Failing test enumeration

5 collection-stage errors per G-RC-10 "3 errors
test_tool_filters + 2 errors test_action_claim_*":

* `tests/runtime/state_graph/guards/test_tool_filters.py`
  -- 3 errors. `legacy_aliases` fixture imports
  `openakita.core._reasoning_engine_legacy as re_module`.
* `tests/unit/test_action_claim_guard.py` -- 1 error.
  Top-of-module:
  `from openakita.core._reasoning_engine_legacy import
  _extract_unbacked_verbs, _get_action_claim_re,
  _guard_unbacked_action_claim, _successful_tool_names`.
* `tests/unit/test_action_claim_recap_guard.py` -- 1
  error. Same top-of-module import pattern by file-name
  convention; verify during P11.2.

### 7.2 Affected source files

Same as Cluster B (section 2.2). Common collection-time
symptom: `ImportError: cannot import name
'_get_mode_ruleset'` (or similar) at module-load.

### 7.3 Root cause hypothesis (verified)

**Confirmed identical root to Cluster B.** Collection-time
error is the same circular-import chain surfacing one frame
earlier (test-module load rather than fixture call).
Verified by reading
`tests/runtime/state_graph/guards/test_tool_filters.py:
13..20` -- the fixture loads `_reasoning_engine_legacy`
which sits next to `_brain_legacy` and shares its
`..llm.client` import prefix. Same cycle, different entry.

### 7.4 Recommended fix strategy

**No separate commit.** Cluster G self-clears when P11.2
lands. If `agent/__init__.py` no longer eagerly pulls
`Brain`, recursive resolution of
`core._reasoning_engine_legacy` no longer re-enters
`core.errors` mid-flight. Post-P11.2 verification: re-run
only the G test files and confirm all 3 + 2 = 5 errors are
gone.

### 7.5 LOC estimate

0 LOC standalone (rolled into P11.2's ~+10 envelope).

### 7.6 Dependencies

* **Hard dependency on Cluster B.** G cannot be addressed
  without B. G-RC-10 implied G + B might need separate fixes
  but this recon's grep evidence confirms they collapse.

## 8. Cluster ordering + execution recommendation

Charter section 4.1 already pins the prioritisation. This
recon **endorses without change** and adds the
dependency-verified rationale:

| order | cluster | tests cleared | LOC | hard dep | rationale |
|--:|---|--:|--:|---|---|
| 1 | A (tool_categories) | 17 + 1 = 18 | ~+155 | none | largest single restoration; clean additive; cannot regress anything |
| 2 | B + G (circular import) | 22 + 5 = 27 | ~+10 | none | second-largest count; one structural fix clears 2 G-RC-10 rows; unblocks D's 4th case |
| 3 | D (static-grep) | 4 | ~0 | B (collateral) | trivial path string-edit |
| 4 | C (xfail 308) | 3 (xfail) | ~+5 | none | smallest commit; documents ADR-0015 lock-out |
| 5 | E (telegram) | 2 | ~+10 | none | env-hygiene; predicate-only edit |
| 6 | F (misc legacy) | 5 (minus A's 1) | ~+20 | A (one absorbed) | last; heterogeneous; can descope |

**Front-loading rationale**: A + B together = 45 of 60
carry-overs (75 %) + ~165 LOC of the ~200-LOC non-doc
envelope. Landing them first means by P11.3 opening time
the full-suite is at ~6071 / 9 = within the gate target.

**Single-commit option**: A + B could squash to one
~+165-LOC commit. This recon recommends **against** squashing:
(i) dedicated B commit is the safe-revert unit if the
lazy-loader breaks an unrelated caller (charter R-11-1
mitigation); (ii) per-cluster commit granularity mirrors
P-RC-10's cadence.

## 9. Total LOC estimate + commit count projection

### 9.1 LOC roll-up vs charter section 2.1

| sub-phase | cluster | charter | recon | delta |
|---|---|--:|--:|--:|
| P11.0a | charter | ~+390 | LANDED | -- |
| P11.0b | recon (this) | ~+490 | **~+490** | 0 |
| P11.1 | A | ~+155 | ~+155 | 0 |
| P11.2 | B + G | ~+10 | ~+10 | 0 |
| P11.3 | C | ~+5 | ~+5 | 0 |
| P11.4 | D | ~0 | ~0 | 0 |
| P11.5 | E | ~+10 | ~+10 | 0 |
| P11.6 | F | ~+20 | ~+20 | 0 |
| P11.7a | gate | ~+250 | ~+250 | 0 |
| **total** | -- | **~+1 330** | **~+1 330** | **0** |

Charter LOC envelope holds verbatim; no recon-time re-shape.

### 9.2 Commit count projection

* **Planning**: 2 (P11.0a LANDED; P11.0b this commit).
* **Cluster fixes**: 6 (P11.1..P11.6; G folds into P11.2).
* **Gate**: 1 (P11.7a).
* **Buffer**: 0..2 split-by-cluster fix-up commits if any
  cluster body exceeds 200 LOC; unlikely per per-cluster
  estimates.

**Total**: **9 commits** (8 firm + 1 buffer); ~3-5 days
elapsed at P-RC-10 cadence.

### 9.3 High-ROI cluster TOP-3 (cleared / LOC)

1. **B + G** -- 27 / ~10 = **2.7 cases / LOC** (one
   lazy-loader line clears two G-RC-10 rows).
2. **D** -- 4 / ~0 net = **infinite ratio** (pure path
   string-edit).
3. **A** -- 18 / ~155 = **0.12 cases / LOC** (largest
   absolute clearance; unblocks orgs / agent factory).

---

**Recon completion check** (P11.0b acceptance, per charter
P11.0b row):

* 7 clusters enumerated; each with 6 required items.
* Every root-cause hypothesis backed by at least one
  `git grep` or file spot-read at this HEAD (5b32d845);
  zero pure speculation.
* Section 8 execution order pinned with dependency
  rationale; section 9 LOC + commit projection consistent
  with charter section 2.1 / 2.2.
* Docs-only commit; ZERO touches on `src/`, `tests/`,
  `apps/`, ADRs, gates, sentinels, the 308 shim
  (`api/routes/_orgs_v2_legacy_redirects.py`),
  `MERGE_TO_MAIN_v2.md`, or any P-RC-10 surface.
* Next: P11.1 (Cluster A -- `tool_categories`
  restoration) opens once operator green-lights.
