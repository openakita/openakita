# omni-post CHANGELOG

## [Unreleased]

- No pending changes.

## [0.2.0] — 2026-04-25

Release package: `omni-post-0.2.0.zip`.

### Changed — release metadata

- `plugin.json` now advertises `0.2.0` and targets Plugin API `~2`, matching
  the current full-stack UI plugin contract.
- README metadata now describes the Sprint 1-4 release as the packaged
  baseline.

## Sprint 4 (2026-04-24)

Sprint 4 closes the roadmap: a compatibility engine that lets users
reuse their daily browser via `MultiPost-Extension`, a daily selector
self-heal probe with throttled IM alerts, a structured MDRM publish
memory for downstream recommendation, and a real Settings tab.

### Added

- `omni_post_engine_mp.MultiPostCompatEngine`:
  - Stateless choreographer — the backend never touches the DOM.
  - `build_mp_payload` translates our task row into the
    `MULTIPOST_EXTENSION_REQUEST_PUBLISH` contract (with the douyin ↔
    tiktok, rednote ↔ xiaohongshu id mapping and a deliberate no-cookie
    audit in tests).
  - `dispatch` broadcasts `mp_dispatch`, then awaits `ack` with a
    configurable timeout; refuses to start when the extension is
    missing / outdated / untrusted.
  - `record_status` / `snapshot_status` expose the latest probe verdict
    to the UI and to `pipeline._resolve_engine`.
- `omni_post_selfheal`:
  - `probe_platform` + `run_probe_cycle` run a pluggable per-selector
    probe, aggregate hit rate, persist to `selectors_health`.
  - Alerts are throttled by `ALERT_COOLDOWN` (24 h) to avoid pager
    fatigue; the default notifier broadcasts a `selector_alert` UI
    event so any IM bridge plugin can pick it up.
  - `SelfHealTicker` runs the cycle every `selfheal_interval_hours`
    (default 24 h) in a dedicated background task.
- `omni_post_mdrm.OmniPostMdrmAdapter`:
  - Thin facade over `api.get_memory_manager()` / `.get_brain()` /
    `.get_vector_store()`, mirroring the idea-research pattern.
  - `PublishMemoryRecord` shapes every publish outcome into
    `SemanticMemory(subject="omni-post:publish:{platform}:{account}",
    predicate="success|failure:{kind}", tags=[...platform, account,
    hour, weekday, engine, asset, error])`.
  - Tolerant by design: missing API, raising getters, and missing
    `add_memory` signatures all downgrade to a `"skipped"` marker.
- `pipeline._resolve_engine` chooses between Playwright and MultiPost
  per-task based on `task.engine` / `settings.engine` and the current
  MP availability snapshot; `_write_publish_memory` now routes through
  the MDRM adapter first and keeps a legacy `api.write_memory` fallback.
- Plugin lifecycle wires both tickers (`SelfHealTicker` gated by
  `settings.enable_selfheal`) and the MDRM adapter; unload stops them
  cleanly before closing the engine + DB.
- FastAPI routes: `GET/POST /mp/status`, `GET /mp/pending`, `POST /mp/ack`.
- UI Tab 6 "Settings":
  - `MultiPostGuide` — PING probe (via `window.postMessage` with 3 s
    timeout), installed/version/trusted-domain chip row, install
    wizard + trust-domain copy when misconfigured.
  - Engine card (auto / pw / mp), Runtime card (global & per-platform
    concurrency, retries, cooldown, headless toggle), Scheduler card
    (poll seconds, self-heal toggle + interval, Playwright probe
    toggle). Save button PUTs the diff to `/settings`.
- `docs/asset-kinds.md` continues to register `publish_receipt` as an
  Asset Bus contract with a 90-day TTL and no sensitive fields.

### Tests

- `tests/test_engine_mp.py` (18): semver comparison, payload shaping
  with a no-cookies audit, availability gating, dispatch ↔ ack
  rendezvous, duplicate-ack idempotency, timeout returns
  `ErrorKind.TIMEOUT`, pending-dispatches snapshot.
- `tests/test_selfheal.py` (6): probe aggregation, exception-as-failure
  accounting, empty-bundle healthy, cycle alert on hit_rate < 0.6,
  alert cooldown respected, notifier raising doesn't break the cycle.
- `tests/test_mdrm.py` (8): tag / predicate / content shape,
  missing-API / raising-API skip paths, full happy-path memory write,
  exception wrap, fallback to single-arg `add_memory` signature.

### Known

- Real-world MultiPost trust-domain detection depends on the extension
  replying to `MULTIPOST_EXTENSION_CHECK_SERVICE_STATUS`; older builds
  may answer with a different shape — `MultiPostGuide` already tolerates
  several envelopes but may need updates when the extension ships 2.x.
- `_default_selector_probe` is an offline sanity check that verifies
  the bundle shape; a Playwright-backed DOM probe is wired when
  `enable_playwright_probe` is flipped on.

---

## Sprint 3 (2026-04-24)

Sprint 3 adds scheduling (with timezone staggering), matrix publishing
(multi-account × multi-platform with tag-routed copy overrides), the
`publish_receipt` Asset Bus contract, and two new UI tabs (Calendar,
Library).

### Added

- `omni_post_scheduler`: `ScheduleTicker` polls due tasks every
  `scheduler_poll_seconds`, runs them through the pipeline, and marks
  schedules as fired. `stagger_slots` computes timezone-aware kick-off
  times; `fanout_matrix` expands `MatrixPublishRequest` into one task
  row per platform × account with per-tag overrides.
- `omni_post_models.MatrixPublishRequest` + `/publish/matrix` route.
- Task-manager additions: `templates` table, `list_due_schedules`,
  `list_scheduled_tasks_in_range`, `reschedule_task`,
  `create/list/update/delete_template`.
- Pipeline: `_build_receipt_payload` + `_publish_receipt_asset` write
  a full JSON receipt to `data/omni-post/receipts/<task_id>.json` and
  publish a 90-day TTL asset on the host Asset Bus
  (`asset_kind="publish_receipt"`, `shared_with=["*"]`).
- `docs/asset-kinds.md` documents the new contract plus the existing
  video / cover / article_draft / subtitle_pack kinds.
- UI Tab 4 "Calendar": week view, timezone-aware rendering, same-account
  conflict flag (< 30 min), reschedule via PUT `/calendar/{task_id}`.
- UI Tab 5 "Library": segmented control Assets ⇄ Templates, asset
  filters (all / video / image / audio / article), template CRUD +
  tags + kind filters, TemplateAddModal with JSON or free-form body.

### Tests

- `tests/test_scheduler.py`: stagger_slots timezone handling,
  fanout_matrix precedence, ScheduleTicker due-task triggering.
- `tests/test_receipt.py`: receipt payload shape, disk + bus publish,
  degradation when bus is unavailable.
- `tests/test_calendar_templates.py`: calendar range query, reschedule
  refuses running tasks, template CRUD with kind validation.

---

## Sprint 2 (2026-04-24)

Sprint 2 widens the platform coverage, hardens the cookie vault, adds
retry + half-auto fallback, and delivers the Account Matrix tab.

### Added

- Seven more platform bundles: `wechat_video` (with a dedicated
  micro-frontend adapter accounting for
  [MultiPost-Extension issue #166](
  https://github.com/leaperone/MultiPost-Extension/issues/166)),
  `kuaishou`, `youtube`, `tiktok`, `zhihu`, `weibo`, `wechat_mp`.
- `CookiePool.probe_lazy` now feeds a Playwright-backed health probe
  when `enable_playwright_probe=true`, following the spirit of
  [issue #207](https://github.com/leaperone/MultiPost-Extension/issues/207).
- Pipeline exponential-backoff retry + `auto_submit` half-auto fallback
  after `auto_submit_fail_threshold` failures
  (see [issue #198](https://github.com/leaperone/MultiPost-Extension/issues/198)).
- Screenshot replay on every terminal failure, cookies redacted.
- UI Tab 3 "Accounts": `AccountMatrixCard` + per-account recently-used
  asset history and quota breakdown.

---

## Sprint 1 skeleton (2026-04-24)

Sprint 1 delivers the backbone: plugin scaffolding, data model, asset
pipeline, Playwright engine base, and the first two UI tabs. Enough to
post to 3 platforms end-to-end on a human-triggered flow. Sprints 2–4
expand platforms, scheduling, Handoff Schema, MultiPost Compat engine,
MDRM memory and self-healing selectors.

### Added

#### Skeleton & manifest

- `plugin.json`: sdk `>=0.7.0,<0.8.0`, 12 permissions, 14 tools, 2
  UI entries (main + settings).
- `plugin.py`: `PluginBase` subclass; 22+ FastAPI routes (publish / tasks /
  accounts / assets / settings / upload / sse); 14 LLM tools registered;
  `on_unload` cancels in-flight pipelines and closes the Playwright
  engine + SQLite connection.
- `requirements.txt`: explicit `cryptography>=42.0.0` for the Fernet
  cookie vault; Playwright + aiosqlite are reused from host.

#### Data model

- `omni_post_models.py`:
  - `ErrorKind`: 9 standard (`network` / `timeout` / `rate_limit` / `auth` /
    `not_found` / `moderation` / `quota` / `dependency` / `unknown`) + 4
    omni-post specific (`cookie_expired` / `content_moderated` /
    `rate_limited_by_platform` / `platform_breaking_change`).
  - `ERROR_HINTS`: bilingual (zh/en) hints for every kind.
  - `PlatformSpec` for 10 target platforms (build_catalog()).
  - Pydantic v2 models with `extra="forbid"`: `PublishPayload`,
    `PublishRequest`, `ScheduleRequest`, `AccountCreateRequest`,
    `SettingsUpdateRequest`.

- `omni_post_task_manager.py`:
  - 7 tables: `tasks`, `assets`, `asset_publish_history`, `accounts`,
    `platforms`, `schedules`, `selectors_health`.
  - aiosqlite + WAL, explicit indexes on hot fields.
  - `UNIQUE(platform, account_id, client_trace_id)` on tasks enforces
    client-side idempotency.
  - Strict whitelist in `update_task_safe` and `update_asset_safe` to
    stop SQL-injection surface.

#### Asset pipeline (chunked upload + dedup)

- `omni_post_assets.UploadPipeline`:
  - 5 MB chunked PUT (`init_upload` / `write_chunk` / `finalize`).
  - MD5-based "秒传" dedup — init short-circuits when client supplies a
    hint matching an existing asset; finalize also re-checks before
    writing a second copy on disk.
  - ffprobe metadata extraction and ffmpeg thumbnail (00:00:01.000,
    scaled to max 480 px), both best-effort — missing binaries log once
    and downgrade to `NULL`.
  - `sweep_stale_uploads()` reclaims space after a host restart.

#### Cookie vault

- `omni_post_cookies.CookiePool`: Fernet symmetric encryption keyed by
  a per-install `identity.salt` file; `seal()` / `open()` are the only
  public surface; `probe_lazy()` does on-demand health checks (returns
  `HealthStatus.unknown` in S1, to be wired up in S2).

#### Playwright engine base

- `omni_post_engine_pw.PlaywrightEngine`:
  - Single Chromium launched per-engine; one `BrowserContext` per task
    with `user_data_dir` isolated by `(platform, account_id)`.
  - Anti-fingerprinting: UA / viewport / `navigator.webdriver` patch /
    timezone / locale.
  - Screenshot on failure with cookie-token redaction
    (`_COOKIE_TOKEN_PATTERN`).
  - `GenericJsonAdapter` interprets declarative JSON steps (shadow-DOM
    traversal, iframe drill, wait-for-selector, file upload,
    contenteditable fill, click) — learned the hard way from
    [MultiPost-Extension issue #166](
    https://github.com/leaperone/MultiPost-Extension/issues/166).
- `omni_post_adapters/base.PlatformAdapter`: abstract class with
  `precheck` / `fill_form` / `submit`; `load_selector_bundle` parses and
  validates JSON bundles; `url` is optional on actions like `submit`
  that piggyback on `fill_form`'s open page.
- `omni_post_selectors/`: 3 platform bundles delivered in S1
  (`douyin.json` / `rednote.json` / `bilibili.json`).

#### Pipeline orchestration

- `omni_post_pipeline.OmniPostPipeline`: central orchestrator,
  exponential backoff (configurable `max_retries` / `base_backoff_s`),
  auto-submit fallback on late-stage failures; writes
  `asset_publish_history`, emits SSE `plugin:omni-post:task_update`,
  publishes `publish_receipt` to the Asset Bus (`shared_with=["*"]`),
  writes MDRM nodes (`platform × account × hour × success`).

#### UI (Tab 1 Publish + Tab 2 Tasks + UploadDock + 4 StubTabs)

- `ui/dist/index.html` ≈ 1500 lines: React 18 + Babel standalone single
  file, 1:1 UI Kit parity with avatar-studio (bootstrap / styles /
  icons / i18n / markdown-mini).
- `UploadDockProvider` context powers a global upload queue visible
  across all tabs; progress / dedup / error states are surfaced in the
  bottom-right dock.
- `PublishTab`: asset select (upload or pick existing) + caption +
  description + tags + platform matrix (S1 3 platforms) + account
  selector + submit.
- `TasksTab`: filterable task list + `TaskDrawer` with payload / error /
  screenshot.
- `StubTab` placeholders for `Accounts` / `Calendar` / `Library` /
  `Settings` with "coming in Sprint N" copy.
- `I18N_DICT` with the `omniPost.*` namespace, zh/en parity.

#### Tests

- `tests/test_models.py`: ErrorKind ↔ ERROR_HINTS mapping parity,
  `OmniPostError` defaults, catalog unique ids, Pydantic
  `extra="forbid"` enforcement.
- `tests/test_task_manager.py`: CRUD + idempotency on
  `(platform, account_id, client_trace_id)` + whitelist guard.
- `tests/test_cookies.py`: Fernet encrypt/decrypt roundtrip + salt
  stability across reinstantiations.
- `tests/test_assets.py`: single/multi-chunk upload, MD5 dedup on
  finalize, init-time short-circuit on md5_hint.
- `tests/test_selectors.py`: all three bundles validate against the
  adapter schema.

### Known

- Sprint 2 will land the remaining 7 platforms, a Cookie health probe
  with auto-refresh, semi-automatic fallback, and the Account Matrix
  tab.
- Sprint 3 will land scheduling, timezone staggering, matrix mode, and
  the Calendar + Library tabs.
- Sprint 4 will land the MultiPost Compat engine, `MultiPostGuide`,
  self-healing selector probes, IM alerts on hit-rate drops, MDRM
  writes, and the full Settings tab + integration tests.

### Compatibility

- Zero `openakita_plugin_sdk.contrib` imports.
- Zero `/api/plugins/_sdk/*` host-mount references.
- Zero `from _shared import ...`.
- `requires.sdk` locked to `>=0.7.0,<0.8.0`, consistent with every
  current first-class plugin.
- UI Kit (`ui/dist/_assets/*`) is byte-for-byte identical to the
  `avatar-studio` copy, so both plugins share theme tokens, dark mode
  semantics, and the i18n interface.
