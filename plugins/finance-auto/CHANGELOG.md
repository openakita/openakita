# Finance-Auto Plugin Changelog

All notable changes to the OpenAkita finance-auto plugin are recorded
here.  Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the plugin
adheres to [Semantic Versioning](https://semver.org/).

This file was started in the round-2 optimisation pass (audit §11
item 3) — entries before v1.0 are reconstructed from git history and
the round-1 / round-2 audit reports; entries from v1.0 onwards are
written commit-by-commit.

## [Unreleased] — v1.0 RC (round-2 optimisations)

### Added

- **`scripts/run_all_acceptance.py`** — single CI entry point that
  runs all 10 acceptance scripts in order, captures per-script
  `{exit_code, elapsed_ms, natural_exit, stdout_tail, stderr_tail}`,
  writes an aggregate JSON, and returns one exit code for the whole
  plugin.  Closes audit §11 item 2 ("`m3_closing_acceptance.py` is an
  orphan island, no CI gate invokes it").
- **`CHANGELOG.md`** (this file) — closes audit §11 item 3 (route
  additions had no changelog trail).
- **`CONTRIBUTING.md`** + **`scripts/check_territory.py`** — territory
  guard for the plugin.  Closes audit §11 item 4 (`38b46b3f orgs_v2`
  9-file +2278-line commit was unrelated to fix-round-1 and drifted
  into the audit window).
- **TODO: CI hook** — `.github/workflows/ci.yml` still does not call
  `run_all_acceptance.py`.  Touching that workflow lives in the
  repo-wide CI territory (`apps/setup-center`-adjacent), so the wiring
  is deferred to the next PR that owns the global workflow file.  Add
  a single `run:` step that invokes
  `d:\OpenAkita\.venv\Scripts\python.exe plugins/finance-auto/scripts/run_all_acceptance.py`.

### Changed

- **`manual_inputs` PUT** (`PUT /orgs/{id}/periods/{pid}/manual-inputs/{key}`)
  now **requires** `expected_version` in the request body.  Missing
  token → HTTP 409 `{"error":"missing_expected_version", ...}`; empty
  slots must echo 0, updates must echo the live version.  The opt-in
  fallback the M3 audit §2.4 flagged as a silent-overwrite race is
  deleted.  Closes audit §11 item 1.
- **`ReviewWorkflowService.resolve_comment`** has the same contract:
  `expected_version` is mandatory; missing token → 409
  `missing_expected_version`; the opt-in fallback is deleted.
  Already-resolved comments stay idempotent (no UPDATE executed) so
  retries are safe.
- Existing acceptance scripts (`m1_w3_acceptance.py`,
  `m2_biz_acceptance.py`) and the manual_inputs / comments test
  modules updated to pass `expected_version` on every PUT.

## [1.0.0 — fix-round-1 batch] — 2026-05-24 (HEAD `053c8ab6`)

This is the work captured in `_finance_plugin_audit_report.md` (round
1, Yellow) and validated by `_finance_plugin_audit_report_round2.md`
(round 2, Green).  Reconstructed from commits
`ff2bf79f..053c8ab6`.

### Fixed (P1 — must-fix before RC)

- **P1-A** Tauri native commands now invoked from the frontend.
  `apps/setup-center/src/lib/native/finance-native.ts` (216 lines) +
  `plugin-bridge-host.ts` route `bridge:finance-native-invoke`; four
  commands wired:
  - `show_finance_consent_dialog`
  - `finance_system_info`
  - `finance_show_notification`
  - `finance_pick_save_path`
  Web fallback returns `{kind:"unsupported"}` so the browser bundle
  degrades cleanly. (commit `22b31de5`)
- **P1-B** Three previously dead-route views rendered + wired:
  - `ReclassificationView` →
    `GET /orgs/{id}/reclassification-rules` returns 200
  - `CrossPeriodView` →
    `GET /orgs/{id}/cross-period-checks` returns 200
  - `CashFlowView` →
    `GET /orgs/{id}/cash-flow/keys` returns 200
  (commits `3b33786a`, `2d19f85f`, `dea1cbf1`)
- **P1-C** `notes_generator` stubs replaced with real queries against
  `trial_balance_rows`; new `_aggregate_account_aux()` helper +
  `_RELATED_PARTY_KEYWORDS` for the related-party scan.
  (commit `9d9a9b5b`)
- **P1-D** Key rotation now covers `parse_issues.__enc_blob__`
  (`_EMBEDDED_BLOB_TABLES` + `_reencrypt_embedded_blob()` in a single
  BEGIN/COMMIT). (commit `b62af341`)
- **P1-E** UI bundle's stale `mock 模式 / 待注册 / 尚未上线` text
  removed from every visible JSX node; the HTML comment lineage marker
  required by `m3_ui_acceptance.py` check #6 is preserved.
  (commit `939dbe57` + lineage preservation `01ea9820`)

### Fixed (P2 — should-fix before RC)

- **P2-1** `test_ai_scenarios` expected 6 scenarios; bumped to 9 after
  M3 raw-AI added three new scenarios (`raw_notes_draft`,
  `raw_nl_query`, `raw_audit_opinion`).  Assertion now exact-equals 9
  via `sorted(==)` and the test is renamed
  `test_registry_lists_all_scenarios`. (commit `60eed31a`)
- **P2-2** `manual_inputs` UPDATE gained
  `WHERE id=? AND version=?` (opt-in in round-1, **strict-enforced in
  round-2**, see [Unreleased] above). (commits `276fdfcf` → `b7128e4d`)
- **P2-3** Pydantic models for 5 M3 schema tables (`NoteTemplateModel`,
  `NoteDocumentModel`, `ReportNoteModel`, `PeerBenchmarkModel`,
  `PeerComparisonResultModel`) + matching `Literal` aliases.
  (commit `93cff591`)
- **P2-4** Unit tests for 3 M3 services
  (`test_notes_generator_real_data.py`,
  `test_peer_comparison_service.py`,
  `test_key_rotation_parse_issues.py`) — total +728 lines.
  (commit `7105ce3b`)
- **P2-5** `m2_closing_acceptance.py`, `m3_closing_acceptance.py`,
  `m3_notes_peer_acceptance.py` switched to `os._exit(rc)` after
  flushing stdout/stderr so the non-daemon ASGI worker thread spawned
  by `TestClient.websocket_connect` cannot wedge the interpreter on
  shutdown. (commit `c1f2e853`)
- **P2-6** `comments` table optimistic lock — `resolve_comment()`
  added the `WHERE id=? AND version=?` UPDATE (opt-in in round-1,
  **strict-enforced in round-2**, see [Unreleased] above).
  (commits `c9e07817` → `b7128e4d`)

### Notes on the "route count" delta

The round-1 audit reported "90 → 94" routes but this was a counting
variance, not a real API surface change.  Confirmed via `git diff` and
a fresh in-process FastAPI startup at both `ff2bf79f` and HEAD: the
finance-auto router still exposes **89 `/api/plugins/finance-auto/*`
endpoints + 1 WebSocket = 90 reachable routes**.  No HTTP route was
added or removed in fix-round-1; what _was_ newly invoked from the
frontend is the **four Tauri native commands** (see P1-A), which are
not HTTP routes at all — they are postMessage bridges and explain the
"+4" delta the round-1 counter attributed to routes.

## v0.x → v1.0 RC functional delta (≤ 30 line summary)

| Capability                                | v0.x design       | v1.0 RC implementation              |
| ----------------------------------------- | ----------------- | ----------------------------------- |
| Trial-balance upload + parse              | spec only         | shipped (W1)                        |
| Encrypted at-rest storage (KeyManager)    | spec only         | shipped (W1) + rotation (M3 Infra)  |
| Balance sheet generation                  | spec only         | shipped (W2 Stage 4)                |
| Excel export                              | spec only         | shipped (W2 Stage 4 / openpyxl)     |
| VAT (golden-tax IV) upload                | spec only         | shipped (W2 Stage 5)                |
| Audit-template render                     | spec only         | shipped (W2 Stage 6)                |
| Industry overlays (3 + general)           | spec only         | shipped (W3 Stage 5)                |
| Manual inputs (7 cash-flow aux slots)     | spec only         | shipped + **strict optimistic lock**|
| Cross-period validation                   | spec only         | shipped (W3 Stage 3)                |
| Cash-flow indirect engine + persist       | spec only         | shipped + report (W3 Stage 4)       |
| Reclassification rules + preview / apply  | spec only         | shipped (M2 Biz)                    |
| Consolidation (members + eliminations)    | spec only         | shipped (M2 Biz)                    |
| Review workflow + comments                | spec only         | shipped + **strict comments lock**  |
| AI scenarios (6 desensitised + 3 raw)     | spec only         | 9 scenarios shipped (M2 AI + M3 raw)|
| AI consent dialog (Tauri native)          | spec only         | shipped + wired from frontend       |
| Notes generator (templates + sections)    | spec only         | shipped, **real-data backed**       |
| Peer benchmarks + comparison              | spec only         | shipped (M3 notes / peer)           |
| Key rotation (incl. embedded blobs)       | spec only         | shipped (M3 Infra)                  |
| Backup / restore (passphrase-gated)       | spec only         | shipped (M3 Infra)                  |
| Tauri integration (4 native commands)     | spec only         | shipped + invoked end-to-end        |
| 10 acceptance scripts                     | spec only         | shipped + **single CI runner**      |

The plugin is ready for v1.0 RC tagging once the CI hook
(`run_all_acceptance.py`) is wired into the global workflow.  No P0
bugs remain open; the only deferred work is the CI step itself, which
is one-line and explicitly scoped out of the finance-auto territory.
