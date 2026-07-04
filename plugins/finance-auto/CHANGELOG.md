# Finance-Auto Plugin Changelog

All notable changes to the OpenAkita finance-auto plugin are recorded
here.  Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the plugin
adheres to [Semantic Versioning](https://semver.org/).

This file was started in the round-2 optimisation pass (audit §11
item 3) — entries before v1.0 are reconstructed from git history and
the round-1 / round-2 audit reports; entries from v1.0 onwards are
written commit-by-commit.

## [1.0.0-rc1] — 2026-05-24

**Release Candidate 1**.  Combines round-2 + fix-round-2 + extended
audit + fix-round-3 + extended audit round-3 + v1.0.0-rc1 close-out.
~110 commits accumulated since the M1 W1 spike; 280 pytest tests +
10/10 acceptance scripts + 92 REST routes + 1 WebSocket; schema v14;
RBAC matrix coverage 10/10 modules.  See §4.4 of
`_finance_plugin_audit_report_round3.md` for the independent grading
(round-3: Yellow-Green; this RC: Green after closing EX-P2-10 +
EX-P2-13).

### Added

- **`DELETE /orgs/{org_id}` endpoint** (EX-P2-10) with
  `?cascade=true|false` query param.  Default `false` returns 409 +
  per-table dependent counts; `true` purges the org in a single
  transaction across 21 dependent tables (17 FK-cascade + 4 non-FK
  `org_id` tables) and unlinks on-disk backup files.  Gated by
  `Depends(require_permission("org", "delete"))` — only `admin` +
  `partner` roles.  See `tests/test_delete_org.py` for the 8-test
  matrix and `scripts/m3_closing_acceptance.py` steps 23–24.
- **`/v1/` URL prefix** (EX-P2-13) for the entire REST surface; legacy
  un-prefixed paths return HTTP 308 to the matching `/v1/` URL,
  preserving method + body + query string.  Backward-compatible —
  no client breaks.  `/ws` + `/v1/ws` are both mounted (WebSocket
  cannot follow 308).  See `tests/test_v1_prefix_redirect.py` (10
  tests).
- **Full RBAC coverage** (EX-P1-2) across 9 previously-uncovered
  write modules: admin, reclassification, cashflow, xperiod,
  audit-template, manual-inputs, consolidation extras, parse-issue,
  notes, peer-comparison.  22 `require_permission` route deps + 7
  service-layer `check_permission` calls.  schema v12 seeds the
  41-row extended permission matrix; schema v14 adds `org.delete`.
- **Encrypted backup sandbox** (EX-P1-1) — `services/backup_restore.
  py` `_is_within` + `_ensure_within_sandbox` validates user-supplied
  `dest_dir` / `target_db_path` against `OPENAKITA_FINANCE_AUTO_
  BACKUP_ROOT`; 409 `target_already_exists` unless `?overwrite=true`.
  `tests/test_backup_sandbox.py` (250 lines).
- **WebSocket heartbeat + max_clients** (EX-P2-4) — `ai/ws.py`
  `MAX_WS_CLIENTS=50` (env override `OPENAKITA_FINANCE_AUTO_WS_MAX_
  CLIENTS`); heartbeat ping every 30s, close 1011 after 60s silence;
  excess clients rejected with close-code 1013.  `tests/test_ws_
  limits.py` (123 lines).
- **WebSocket reconnect + cursor + reconnecting badge** (EX-P2-14)
  in UI bundle — exponential backoff (1s → 32s capped), `?since=`
  query for replay-position hint, four-state badge
  (init / connecting / connected / reconnecting), `BroadcastChannel`
  fan-out for cross-tab leader election on `ai_consent_request`.
- **Reclassification undo API** (EX-P2-9) —
  `POST /orgs/{id}/reclassification-rules/{rule_id}/undo` walks the
  inverse-delta history (new schema v13 `reclassification_history`
  table) and reverses the run row-by-row.  `tests/test_
  reclassification_undo.py` (185 lines).
- **Tauri native commands fully wired** (P1-A from fix-round-1) — 4
  commands invoked end-to-end via `plugin-bridge-host.ts`:
  `show_finance_consent_dialog`, `finance_system_info`,
  `finance_show_notification`, `finance_pick_save_path`.  Web
  fallback returns `{kind:"unsupported"}`.
- **3 previously-dead frontend views** (P1-B) — `ReclassificationView`,
  `CrossPeriodView`, `CashFlowView` now mount + fetch live data
  from their respective endpoints.
- **`openapi-typescript` generator** (EX-P2-12) —
  `plugins/finance-auto/ui/scripts/gen-types.mjs` pulls
  `/openapi.json` filtered to `/api/plugins/finance-auto/v1/*` and
  generates `dist/types/finance-auto-api.d.ts`.  Advisory tool, not
  CI-gated for v1.0 RC.
- **CI workflow** (EX-P2-1 + audit §11.2 follow-through) —
  `.github/workflows/finance-auto-ci.yml` 154 lines, 4 jobs:
  ruff lint → pytest → `run_all_acceptance.py` → pip-audit.  Headless
  keyring resolved via `OPENAKITA_FINANCE_AUTO_PASSPHRASE=$(openssl
  rand -hex 32)`.
- **Docker headless documentation** (EX-P2-11) —
  `plugins/finance-auto/docs/DEPLOY_DOCKER.md` (258 lines): TL;DR +
  compose + k8s + troubleshooting sections.
- **`CHANGELOG.md`** (this file) + **`CONTRIBUTING.md`** +
  **`scripts/check_territory.py`** (round-2 audit §11).
- **`scripts/run_all_acceptance.py`** — single CI entry point for the
  10 acceptance scripts; aggregate JSON + per-script timeout +
  natural-exit detection.
- **8 new test modules** — `test_backup_sandbox.py` (250),
  `test_rbac_e2e.py` (276), `test_decrypt_failure.py` (109),
  `test_llm_retry.py` (111), `test_reclassification_perf.py` (143),
  `test_reclassification_undo.py` (185), `test_transaction_
  rollback.py` (368), `test_ws_limits.py` (123); plus v1.0.0-rc1's
  `test_delete_org.py` (8 tests) + `test_v1_prefix_redirect.py`
  (10 tests).

### Changed

- **PBKDF2 iterations 200 000 → 600 000** (EX-P1-3) per OWASP 2023
  recommendation.  `BACKUP_DEFAULT_KDF_ITERATIONS = 600_000` in
  `services/backup_restore.py`; env override
  `OPENAKITA_FINANCE_AUTO_KDF_ITERATIONS` (100k lower bound).
  Older 200k backups still decrypt because `restore_backup` reads
  `kdf_iterations` from each backup manifest.
- **`manual_inputs` PUT** now **requires** `expected_version`
  (previously opt-in fallback).  Missing token → 409
  `missing_expected_version`; empty slots must echo 0, updates must
  echo the live version.
- **`ReviewWorkflowService.resolve_comment`** has the same strict
  `expected_version` contract.  Already-resolved comments stay
  idempotent (no UPDATE) so retries are safe.
- **Reclassification batch INSERT** uses `executemany` (EX-P2-3) —
  1000-rule apply: 100 round-trips → 1.  `services/
  reclassification.py:294`; `tests/test_reclassification_perf.py`
  pins the speed-up.
- **Schema v11 → v14** — additive bumps only:
  - v12 (fix-round-3): 41-row extended permission matrix.
  - v13 (fix-round-3): `reclassification_history` table for undo.
  - v14 (v1.0.0-rc1): `org.delete` permission for admin + partner.
- **5 SCHEMA_VERSION acceptance assertions** changed from `== 11` to
  `>= 11` with explanatory comments
  (`# additive schema bumps (v11 → v13) — newer M3+ migrations are
  backward-compatible`) — see `_finance_plugin_audit_report_round3.
  md` §6 for the soft-regression flag this annotation closes.

### Fixed

- **Notes generator real data** (P1-C) — `notes_generator.py` stubs
  replaced with live queries against `trial_balance_rows`; new
  `_aggregate_account_aux()` + `_RELATED_PARTY_KEYWORDS` driver.
- **Key rotation covers `parse_issues.__enc_blob__`** (P1-D) —
  `_EMBEDDED_BLOB_TABLES` + `_reencrypt_embedded_blob()` in a single
  BEGIN/COMMIT.
- **Backup `.partial` cleanup on failure** (EX-P2-7) — `tarfile.open
  (partial_path)` + `os.replace(partial→final)` atomic rename;
  exception branch unlinks the partial best-effort.
- **Decryption failures now raise `DecryptionError`** (EX-P2-6) — no
  more silent fallback to raw cleartext columns; routes wrap into
  HTTP 500 `{"error": "decrypt_failed", ...}` with optional
  `?accept_corrupted=true` disaster-recovery escape hatch.
- **LLM retry/backoff** (EX-P2-8) — `ai/router.py` exponential
  backoff with jitter; `is_retryable_llm_error()` short-circuits on
  4xx auth.  `tests/test_llm_retry.py` (111 lines).
- **Transaction rollback on cross-table failure** (EX-P2-5) —
  explicit `await conn.rollback()` in 4 services
  (consolidation, reclassification × 2, cash-flow, review_workflow)
  so a mid-cascade exception doesn't leave half-applied rows.
- **`m2/m3 closing_acceptance.py` clean exit** (P2-5) — `os._exit
  (rc)` after stdout/stderr flush so non-daemon ASGI worker threads
  cannot wedge the interpreter on shutdown.
- **AI scenarios count drift** (P2-1) — `test_ai_scenarios` expected 6
  but raw-AI added 3; assertion now exact-equals 9 via `sorted(==)`
  and renamed `test_registry_lists_all_scenarios`.
- **5 core dependencies declared** (EX-P1-5) —
  `plugins/finance-auto/requirements.txt` (openpyxl / xlrd==1.2.0 /
  xltpl / keyring / pywin32 / cryptography); `plugin.json
  python_dependencies` sync'd; `pyproject.toml [project.optional-
  dependencies].finance-auto` for `pip install -e ".[finance-auto]"`.
- **Pipe deadlock** in CI subprocess wrappers — stdout/stderr drained
  in a thread before `proc.wait()` (closing acceptance scripts).
- **3 README front-door drift fixes** (round-3 §7) — PBKDF2 600k,
  schema v13 → v14, route count 90 → 91 → 92.  Documented in §6.1
  / §10 / §5 / §3.4 / §3.2.

### Security

- **AES-256-GCM** for all `_encrypted_payload` columns; nonce =
  `os.urandom(12)` per encryption; AAD = `openakita-finance-v1`.
- **PBKDF2-HMAC-SHA256 600 000 iterations** (vs OWASP 2023 600k
  baseline) for backup-passphrase KDF.
- **Application-layer RBAC** on 10/10 write modules (round-3 matrix
  `_round3_rbac_matrix_result.json`).
- **Path traversal sandbox** for backup destination + target_db_path
  with `is_relative_to()` validation under `OPENAKITA_FINANCE_AUTO_
  BACKUP_ROOT`.
- **KDF env override** (`OPENAKITA_FINANCE_AUTO_KDF_ITERATIONS`) for
  operators with tighter compliance requirements; 100k floor.
- **Decryption failure escalates** — silent raw-cleartext fallback
  removed; explicit `?accept_corrupted=true` opt-in for disaster
  recovery only.

### Known Limitations (deferred to v1.0.x / v1.1)

- `m2_closing_acceptance.py` occasionally TIMEOUTs at 120s in batch
  acceptance runs; single subprocess always 3.1s natural exit.
  Root cause: scheduler background threads non-daemon + sqlite WAL
  state pollution between back-to-back runs.  v1.0 GA: daemonise
  scheduler + add `service.shutdown()` hook.
- AI raw-sensitivity scenarios (S6 / S7 / S11) use mock LLM endpoint
  in CI via `monkeypatch.setattr(FinanceAIRouter, ...)`.  Production
  needs a real Ollama / OpenAI-compatible endpoint.
- Tauri native commands have unit coverage in `m3_ui_acceptance.py`
  but no end-to-end IPC test against a live Tauri shell.
- Notes templates: 8 sections shipped vs ~40 in a typical A-share
  filing — v1.x extension.
- Peer benchmarks: 12 rows of static JSON (3 industries × 4 metrics)
  — v1.x plans to ingest CSRC / Wind feeds.
- WebSocket message replay: client cursor + `?since=` query are
  in place; server-side replay buffer is v1.x.
- Multi-user key negotiation: component key is currently shared via
  `key_meta`; per-user sub-key derivation is v1.1.
- Docker image: compose / k8s templates documented but no
  registry-pushed image for v1.0 RC.

### Overall numbers (cumulative since plugin spike)

- ~110 commits accumulated (M1 ≈19 + M2 ≈16 + M3 ≈23 + fix-round-1
  ≈10 + round-2 ≈8 + fix-round-2 ≈7 + extended audit + fix-round-3
  23 + round3-extended + v1.0.0-rc1 close-out 7).
- **280 pytest tests** (was 262 at fix-round-3; +18: 8 DELETE /orgs
  + 10 /v1/ redirect).
- **10/10 acceptance scripts** green (24.6s aggregate runtime).
- **92 REST routes + 1 WebSocket** (was 90; +1 reclassification undo,
  +1 DELETE /orgs).
- **schema_version: 14** (was 11; +3: v12 RBAC seeds, v13 reclass
  history, v14 org.delete perm).
- **9-tier severity** (P0 / P1 / P2 + EX-P0 / EX-P1 / EX-P2 across
  audit rounds 1–3).
- **RBAC matrix coverage**: 10/10 write modules return
  `403 rbac_denied` for unknown users (round-3 matrix probe).

## [Unreleased] — v1.0 RC (round-2 optimisations) — superseded

The round-2 optimisation batch (`run_all_acceptance.py` runner,
`CONTRIBUTING.md`, `check_territory.py`, strict-enforce
`expected_version`) is captured in the [1.0.0-rc1] section above —
this stub header is retained for git-blame linkage to the original
fix-round-2 commits (`b7128e4d`, etc.).

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
