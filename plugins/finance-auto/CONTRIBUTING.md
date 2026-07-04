# Contributing to finance-auto

This document encodes the **territory boundary** for the finance-auto
plugin.  It exists because of audit §11 item 4: the commit
`38b46b3f orgs_v2` (9 files, +2278 lines) drifted into the
finance-auto fix-round-1 audit window even though it had nothing to do
with finance-auto — it was an `orgs_v2` workstream that happened to
land on the same branch.  The boundary below + the lightweight check
script under `scripts/check_territory.py` make that kind of drift
visible at commit time instead of two audit cycles later.

## 1. Territory boundary — what counts as a finance-auto change

A commit whose subject scope is `finance-auto` (or a recognised
sub-scope, see §3) **must only touch files under these paths**:

| Layer                | Allowed paths                                              |
| -------------------- | ---------------------------------------------------------- |
| Backend (Python)     | `plugins/finance-auto/finance_auto_backend/**`             |
| Tests (Python)       | `plugins/finance-auto/tests/**`                            |
| Acceptance scripts   | `plugins/finance-auto/scripts/**`                          |
| UI bundle (built)    | `plugins/finance-auto/ui/dist/**`                          |
| Plugin docs          | `plugins/finance-auto/CHANGELOG.md`,                       |
|                      | `plugins/finance-auto/CONTRIBUTING.md`,                    |
|                      | `plugins/finance-auto/README.md` (if present)              |
| Tauri native bridge  | `apps/setup-center/src-tauri/src/finance*.rs` (single-     |
|                      | file scope; broader Tauri shell changes need a separate    |
|                      | `feat(setup-center-tauri)` commit)                         |
| Finance JS bridge    | `apps/setup-center/src/lib/native/finance-*.ts` (only the  |
|                      | finance-prefixed bridge module; `plugin-bridge-host.ts`,   |
|                      | `plugin-router.ts`, and other shared plugin infra are NOT  |
|                      | finance-auto territory)                                    |
| Workspace audit docs | `_finance_plugin_*.md`, `_fix_round*.md` (plugin-status    |
|                      | reports that live at repo root by convention)              |

Files **outside** the table are off-limits to a `feat(finance-auto)` /
`fix(finance-auto)` commit.  In particular:

- `apps/setup-center/src/**` (React source) — only the **built**
  bundle `plugins/finance-auto/ui/dist/index.html` lives here, NOT the
  src tree.  If you find yourself editing React source, your scope is
  `feat(setup-center)`, not `feat(finance-auto)`.
- `.github/workflows/**` — repo-wide CI; wiring
  `run_all_acceptance.py` into the workflow is a separate
  `chore(ci): ...` commit.
- `src/openakita/**` — OpenAkita core; the plugin must communicate
  via the public plugin SDK, never reach into the core directly.
- Other plugins under `plugins/**` — strict isolation; if you need a
  cross-plugin contract, ship it through the SDK.

## 2. Sibling-worker template

When multiple workers ship in parallel (the fix-round-1 model), each
should declare its territory **before** starting:

```
Sibling A — backend P1-C / P1-D / P2-1..6
  Territory: plugins/finance-auto/finance_auto_backend/**,
             plugins/finance-auto/tests/**,
             plugins/finance-auto/scripts/m1_w3_acceptance.py (P2 fix only)

Sibling B — frontend P1-A / P1-B / P1-E
  Territory: plugins/finance-auto/ui/dist/index.html,
             apps/setup-center/src/lib/native/finance-native.ts,
             apps/setup-center/src/lib/plugins/plugin-bridge-host.ts

Sibling W — adversarial read-only audit
  Territory: read-only (no commits); writes ONLY to
             _finance_plugin_audit_extended_report.md at repo root
```

If a sibling's territory overlaps another sibling's, **lift the
overlap to a parent coordinator** before either starts — never let
two workers race the same file.

## 3. Commit-message scope conventions

The conventional-commit scope must match the territory:

| Scope                 | When to use                                            |
| --------------------- | ------------------------------------------------------ |
| `finance-auto`        | Backend, tests, mixed plugin changes                   |
| `finance-auto-ui`     | UI bundle changes only (`ui/dist/index.html`)          |
| `finance-auto-tests`  | Test-only changes (`tests/**`)                         |
| `finance-auto-scripts`| Acceptance / migration script changes (`scripts/**`)   |
| `finance-auto-docs`   | Plugin-local doc changes (`CHANGELOG.md`,              |
|                       | `CONTRIBUTING.md`, etc.)                               |

Multi-scope commits (e.g. backend + tests in one commit) use
`finance-auto`.  A commit whose scope is `finance-auto-ui` but whose
diff touches `finance_auto_backend/**` is a **territory violation**
and the territory script will flag it.

## 4. Pre-commit / pre-push usage

Before pushing a finance-auto branch:

```powershell
d:\OpenAkita\.venv\Scripts\python.exe `
    plugins/finance-auto/scripts/check_territory.py `
    --commit-range origin/main..HEAD
```

Exit codes:

* `0` — every commit in the range is either out-of-scope (no
  `finance-auto*` scope) or stays inside its declared territory.
* `1` — at least one commit declared a `finance-auto*` scope but
  modified files outside the territory.  Refactor or re-scope the
  commit before pushing.

The script also prints a **warning** (without failing) when a commit
without a `finance-auto*` scope happens to touch
`plugins/finance-auto/**` — that is the "orgs_v2 drifted into the
audit window" pattern.  Whether it is intentional is a judgement call
for the reviewer.

We deliberately **do not** install this as a mandatory git hook — the
script is opt-in tooling rather than a workflow blocker.  Recommended
hooks for a personal workstation:

```bash
# .git/hooks/pre-push (recommended, not auto-installed)
#!/usr/bin/env bash
exec d:\\OpenAkita\\.venv\\Scripts\\python.exe \
    plugins/finance-auto/scripts/check_territory.py \
    --commit-range origin/main..HEAD
```

## 5. Round-2 audit follow-up checklist

For every PR that lands in finance-auto:

- [ ] All commits scoped `finance-auto*` (or sub-scope, §3)
- [ ] `scripts/check_territory.py --commit-range <base>..HEAD` exits 0
- [ ] `pytest -q plugins/finance-auto/tests/` passes (round-2 baseline:
      217 tests)
- [ ] `scripts/run_all_acceptance.py` exits 0 (10/10 scripts pass)
- [ ] If the change touches optimistic-lock code paths, the
      `expected_version` strict-enforcement contract is preserved
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`

See `_finance_plugin_audit_report_round2.md` §11 for the original
list of items this document closes.
