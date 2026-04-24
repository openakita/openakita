# omni-post CHANGELOG

## [Unreleased] — Sprint 1 skeleton (2026-04-24)

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
