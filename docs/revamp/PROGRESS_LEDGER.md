# Revamp Progress Ledger

<!-- machine-readable phase marker; do NOT remove.
     Parsed by tests/revamp/_ledger.py + tests/parity/test_no_facade.py. -->
current_phase: P-RC-3

> Source of truth for every commit landed on `revamp/v2` during
> the post-RC continuation phases (P-RC-0 → P-RC-8). One row per
> commit, in commit order. Each row is appended *in the same
> commit that produced it* (or the next commit, when the producing
> commit is the row source itself, i.e. commit 4 of P-RC-0 which
> bootstraps this file).
>
> Rules of the ledger (per continuation plan §0.3):
> * append-only — once a row lands on `revamp/v2` it must not
>   be silently rewritten;
> * `LOC delta` and `tests delta` are signed integers,
>   positive = grew, negative = shrank, `0` = unchanged;
> * `ADR refs` lists the ADRs whose §s the commit implements.
>
> Pause points: every 5 commits, re-read `docs/revamp/STATUS.md`
> + this ledger + the relevant section of the original plan
> (`openakita_full_backend_revamp_e6d8610d.plan.md`) and the
> continuation plan
> (`openakita_revamp_continuation_plan_d6192647.plan.md`) before
> opening the next commit.

## P-RC-0 — Truth alignment & drift guardrails

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `b2abc4db` | P-RC-0 #1 | chore(config): align runtime_v2_enabled doc with its default=True | +22 | 0 | ADR-0010 |
| `15da567e` | P-RC-0 #2 | docs(revamp): write rollback.md (plan §7 mitigation, was missing) | +141 | 0 | ADR-0010 |
| `5f5e269e` | P-RC-0 #3 | chore(repo): gitignore local smoke-e2e artifacts; relocate to tests/artifacts/ | +9 | 0 | — |
| `30d5a287` | P-RC-0 #4 | tooling(revamp): add LOC invariant audit + PROGRESS_LEDGER scaffolding | +380 | +2 | — |
| `528db8d1` | P-RC-0 #5 | test(parity): kill facade-self-equivalence false positives | +278 / -18 | +6 | — |
| _this commit_ | P-RC-0 G | docs(revamp): G-RC-0 gate review (post-RC continuation phase 0 done) | +174 (gate note) + STATUS row + ledger fill-in | 0 | — |

## P-RC-1 — IM truly lands on the v2 Supervisor

G-RC-0 was signed; this phase wires canary IM traffic to the v2
supervisor stack. The first two rows (`P1.0a`/`P1.0b`) close the
two nits the P-RC-0 audit raised before the main work begins.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| 80d23766 | P-RC-1 P1.0a | chore(revamp): standardise progress ledger to advertise current_phase | +60 | 0 | — |
| 9b5671ca | P-RC-1 P1.0b | test(revamp): enforce sentinel expiry against current_phase | +85 | +7 | — |
| 89902514 | P-RC-1 #1 | feat(runtime): add session_bridge for session<->org id lookup | +231 | +8 | ADR-0002 |
| b0653a59 | P-RC-1 #2 | feat(runtime): promote channel_routing helper to async dispatch_inbound_message_to_v2 | +398 / -5 | +6 | ADR-0002, ADR-0004 |
| 1c0a69a3 | P-RC-1 #3 | feat(runtime): add im_stream_bridge to relay StreamBus progress to IM channels | +292 | +7 | ADR-0006 |
| fc2558dd | P-RC-1 #4 | feat(channels): replace canary log hook with real v2 dispatch (canary-org gated) | +125 / -30 | 0 | ADR-0002, ADR-0004 |
| a97fa73b | P-RC-1 #5 | feat(channels): plumb IM cancel verb to runtime CancellationToken (per org) | +47 (gw) +147 (test) | +4 | ADR-0002, ADR-0004 |
| fc701385 | P-RC-1 #6 | feat(config): add runtime_v2_canary_orgs allow-list (default empty) | +37 / -2 (cfg) +45 (test) | +5 | ADR-0002 |
| 4d396303 | P-RC-1 #7 | test(integration): e2e canary org runs via Supervisor + cancel + resume | +273 (test) +11 (gw drain fix) | +1 | ADR-0002, ADR-0004, ADR-0006 |
| _this commit_ | P-RC-1 G | docs(revamp): G-RC-1 gate review + STATUS scoreboard update | +224 (gate) + STATUS row + ledger fill-in | 0 | — |

## P-RC-2 — Frontend v2 wiring

G-RC-1 was signed; this phase wires the v2 supervisor stack into
the setup-center frontend (SSE backend, EventSource client,
ProgressLedgerTimeline, OrgChatPanel switch, TemplatePickerDrawer
mount, stale-bundle banner) and closes the two residual risks the
P-RC-1 gate review flagged: drain-on-close in `StreamBus` (#1) and
cold-session `org_id` rehydration in `MessageGateway` (#3).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `b6a77a94` | P-RC-2 P2.0 | chore(revamp): bump ledger current_phase to P-RC-2 + apply N3/N4/N5 discipline doc | +33 / -2 | 0 | — |
| `112534d5` | P-RC-2 P2.1 | feat(runtime): add drain-on-close semantics to StreamBus | +248 / -16 | +4 | ADR-0006 |
| `2d35c0f9` | P-RC-2 P2.2 | feat(channels): rehydrate cold-session org_id from disk in lookup | +153 / -13 | +6 | ADR-0002 |
| `00c783f3` | P-RC-2 P2.3 | feat(api): add GET /api/v2/orgs/{id}/stream (SSE) backed by StreamBus | +400 / -1 | +5 | ADR-0006 |
| `74d565b7` | P-RC-2 P2.4 | feat(setup-center): add v2 stream client (EventSource wrapper) + vitest infra | +316 (src) +1119 (lock, generated) | +5 (vitest) | ADR-0006 |
| `415226a7` | P-RC-2 P2.5 | feat(setup-center): render ProgressLedgerTimeline component | +219 / -2 | +4 (vitest) | ADR-0006 |
| `a9cd8f82` | P-RC-2 P2.6 | feat(setup-center): OrgChatPanel switches to v2 stream when org is v2-bound | +153 / -2 | +2 (vitest) | ADR-0006 |
| `7bd3c29b` | P-RC-2 P2.7 | feat(setup-center): mount TemplatePickerDrawer in OrgEditorView | +169 / -1 | +1 (vitest) | ADR-0006 |
| `0bfad7de` | P-RC-2 P2.8 | feat(setup-center): bump asset version + stale bundle banner | +339 / -1 | +2 (vitest) +3 (pytest) | ADR-0007 |
| _this commit_ | P-RC-2 P2.9 | docs(revamp): G-RC-2 gate review + STATUS scoreboard update | +220 / 0 | 0 | — |

## P-RC-3 — Multi-process-safe v2 persistence + nit cleanup

G-RC-2 was signed; this phase folds in five small tail items the
P-RC-2 audit identified (T1–T5), then adds a SQLite-backed
`SqliteOrgStore` so multi-process v2 deployments can share a
single org catalogue without JSON-write contention. The phase also
ships a JSON->SQLite migration helper, a pluggable backend factory
gated by `settings.orgs_v2_backend`, and a contract suite that
runs identical cases against both backends.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `f26863c5` | P-RC-3 P3.0 | chore(revamp): bump ledger to P-RC-3 + add commit_guard script (T1) | +332 (script+tests+ledger+ruff) | +11 (commit_guard) | — |
| `20021b71` | P-RC-3 P3.1 | docs(revamp): correct G-RC-2 5ms polling wording (T2) | +4 / -2 (gate doc) | 0 | — |
| `69225c0f` | P-RC-3 P3.2 | feat(runtime): add closed-gate to StreamBus + public subscription API (T3+T5) | +156 / -14 | +5 (closed-gate + public-api) | ADR-0006 |
| `7aabdce3` | P-RC-3 P3.3 | feat(runtime): add idle-bus cleanup to StreamRegistry (T4) | +306 / -6 | +6 (cleanup_idle + periodic + reattach) | ADR-0006 |
| `723fd1d5` | P-RC-3 P3.4 | feat(runtime/orgs): add SqliteOrgStore mirroring JsonOrgStore contract | +371 (sqlite_store + tests) | +9 (CRUD + concurrent + reopen + corrupted) | ADR-0010 |
| `fea6a347` | P-RC-3 P3.5 | feat(runtime/orgs): contract suite shared across Json + Sqlite stores | +205 / -1 (contract test + sqlite trailing nl) | +18 (9 cases x 2 backends) | ADR-0010 |
| `a0339d12` | P-RC-3 P3.6 | feat(runtime/orgs): pluggable backend via settings.orgs_v2_backend (json|sqlite) | +138 / -17 (config + factory + tests) | +6 (config + factory dispatch) | ADR-0010 |
| _this commit_ | P-RC-3 P3.7 | feat(scripts): migrate_orgs_v2_json_to_sqlite (idempotent) | +338 (script + tests + rollback) | +5 (migrate fresh/idempotent/dry-run/malformed/empty) | ADR-0010 |

## P-RC-4 — Phase 2 real slim-down: brain / tools / context

_Not started._

## P-RC-5 — Phase 2 real slim-down: reasoning_engine

_Not started._

## P-RC-6 — Phase 2 real slim-down: agent.py

_Not started._

## P-RC-7 — Caller migration + legacy bulk delete

_Not started._

## P-RC-8 — Endgame (renames, docs, acceptance, release)

_Not started._

## Discipline reminders (auto-collected by audits)

These are nits / gotchas the per-phase audits surfaced; they are
documented here so future executors do not re-discover them the hard
way. Each entry is one line; the audit that raised it is named in
parentheses. Once resolved across two consecutive phases, an entry
may be retired from this list.

* **N3** (G-RC-1 P-RC-1 audit): when a commit's ``Files:`` footer says
  ``PROGRESS_LEDGER.md (append ...)``, the ledger row MUST be in the
  same commit -- ``git add docs/revamp/PROGRESS_LEDGER.md`` before
  ``git commit``. No more "next commit fills the prior hash".
* **N4** (G-RC-1 P-RC-1 audit): keep per-commit diff strictly under
  400 LOC. If insertions reach 380+, STOP and split the commit
  before recording it.
* **N5** (G-RC-1 P-RC-1 audit, cosmetic): write commit messages via
  Python (``pathlib.Path("commit_msg.tmp").write_text(msg,
  encoding='utf-8')`` then ``git commit -F commit_msg.tmp``); never
  via PowerShell ``Out-File -Encoding utf8`` -- the latter prepends a
  UTF-8 BOM and the resulting commit subject reads as
  ``\ufefffeat(...): ...``.

* **T1** (G-RC-2 P-RC-2 audit, tightened): before every `git commit` on `revamp/v2`, run `python scripts/revamp_commit_guard.py --staged`. The script
  reads `git diff --cached --numstat`, skips auto-generated
  files (`package-lock.json`, `*.lock`, `*.svg`,
  `docs/revamp/*.json` baselines), warns at >= 380 hand-written
  LOC, and exits 1 at >= 400. No git hook is installed; this is
  a manual operator discipline so a legitimate one-off giant
  commit can still be force-recorded with eyes open.
