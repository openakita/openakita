# Changelog

All notable changes to fin-pulse will be documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

- No pending changes.

## [1.1.0] — 2026-04-25

Release package: `fin-pulse-1.1.0.zip`.

### Added — release packaging

- `plugin.json` and `plugin.py` now advertise `1.1.0` for the hybrid
  fetcher + scheduler dispatch release.
- README metadata calls out the SDK range, Plugin API contract, and bundled
  UI assets expected in the installable plugin zip.

### Changed — ingest path

- **Hybrid NewsNow-first fetchers for CN hot lists.** The 4 high-churn
  Chinese sources (`wallstreetcn` / `cls` / `eastmoney` / `xueqiu`) now
  try the community-run NewsNow aggregator first
  and only fall back to their direct scraper when the aggregator is
  unreachable, returns an empty envelope, or the 300-second public
  cooldown is in effect. Each fetcher records `_last_via` so the pipe-
  line and UI can surface the actual transport as
  `NewsNow / 直连 / 冷却 / 无结果`.
- New `finpulse_fetchers/newsnow_base.py` encapsulates the NewsNow
  envelope parser so the 4 CN fetchers share one surface (expects `status` ∈
  `{"success", "cache"}`, title/URL strip, title dedupe, `mobileUrl`
  fallback).
- `POST /ingest` response now exposes `summary.by_source[id].via` and
  adds `summary.totals.sources_total` / `sources_ok` so the UI can
  render honest counts.
- `finpulse_pipeline.ingest()` auto-promotes `newsnow.mode` to `public`
  **in memory** (never persisted) when any of the 4 CN sources runs so
  hybrid fetchers always have a preferred transport out of the box.
- `finpulse_pipeline.ingest()` no longer strands `no_sources_enabled`
  runs at `status=running`; it now flips the task row to
  `status=skipped` with a consistent `{ok: false, reason, totals}`
  payload.
- `POST /ingest/source/{id}` now wraps the pipeline call in the same
  try/except as `/ingest`, mapping exceptions through
  `finpulse_errors.map_exception` and writing `error_kind` +
  `error_hints` onto the task row so the UI can pin a red pill.

### Changed — Today tab UX

- **Honest ingest feedback.** The misleading "green success + empty
  list" toast is gone. `onIngest` now reads `summary.totals` +
  `summary.by_source` and emits a smart toast: green `新增 X · 更新 Y`
  when rows land, amber `X 源成功 · Y 失败 · Z 源无结果` otherwise.
- **Inline result drawer.** Dismissible card under the filter bar
  rendered after every ingest. Shows one pill per source with colour
  coding (green / amber / red), a `NewsNow / 直连 / 冷却 / 无结果`
  transport badge, item counts, and an expandable error row
  (`error_kind` + message) for failed sources.
- **Split ingest button.** Primary half still fires every enabled
  source; the caret exposes a dropdown to trigger `POST /ingest/source/{id}`
  for the currently filtered source only — handy for probing a single
  scraper without waiting on the whole batch.
- **Better progress UX.** Button swaps to a rotating spinner while
  busy; an indeterminate shimmer bar appears under the filter strip;
  `aria-busy="true"` is set.
- The 8-second `setInterval(load, 8000)` background poll is gone. The
  list refreshes right after each ingest resolves and whenever filters
  change — which both shaved CPU and surfaced the silent bugs the poll
  was hiding.

### Tests

- New `tests/test_fetchers_newsnow.py` covering the NewsNow envelope
  parser, `status="forbidden"` raising, `cache` acceptance, blank
  title/URL drop, and ms-timestamp coercion.
- New `tests/test_fetchers_hybrid.py` exercising the four hybrid paths
  (NewsNow OK / NewsNow empty → direct / NewsNow raises → direct /
  both empty → `via=none`) across all 4 CN fetchers.
- New `tests/test_pipeline_hybrid.py` pins `summary.by_source[id].via`,
  the `no_sources_enabled → status=skipped` transition, and the
  in-memory-only `newsnow.mode` auto-promote.
- `tests/test_smoke.py` now asserts the new i18n strings
  (`today.ingest.done.{green,amber}`, `today.drawer.title`,
  `today.drawer.via.{newsnow,direct,cooldown}`, etc.), the presence of
  the `IngestDrawer` component, the `ingest-split` / `fp-spin` /
  `fp-progress` markup, the `/ingest/source/{id}` wiring, and the
  absence of the dropped `setInterval(load`.

## [1.0.0] — 2026-04-24

First tagged release. Feature-complete against the
`fin-pulse_财经脉动插件_95c17c0d` plan.

### Added — Phase 1 (plugin skeleton)

- `plugin.json` manifest with eight permissions
  (`tools.register` / `routes.register` / `hooks.basic` / `data.own`
  / `channel.send` / `brain.access` / `config.read` / `config.write`)
  and seven declared `provides.tools`.
- `plugin.py` entry registering FastAPI router, agent tools,
  `on_schedule` match predicate, and a lazy async bootstrap.
- `finpulse_task_manager.py` — aiosqlite 4-table schema
  (`tasks` / `articles` / `digests` / `config`) plus a reserved
  `assets_bus` table for V2.0 cross-plugin handoff.
- `finpulse_models.py` — `MODES`, `SOURCE_DEFS`, `SESSIONS`,
  `DEFAULT_CRONS`, `SCORE_THRESHOLDS`.
- `finpulse_errors.py` — nine `error_kind` classifier
  (`network` / `timeout` / `auth` / `quota` / `rate_limit` /
  `dependency` / `moderation` / `not_found` / `unknown`) with ZH + EN hints.
- `ui/dist/index.html` single-page React 18 shell with the
  avatar-studio 5-asset bundle vendored under `_assets/` and
  the hard-contract tokens enforced by
  `tests/test_smoke.py::test_ui_hard_contracts`.

### Added — Phase 2 (ingestion)

- `finpulse_fetchers/base.py` — `NormalizedItem` dataclass,
  canonical URL hashing, `BaseFetcher` ABC, and fetcher registry.
- Eight first-party fetchers: `wallstreetcn`, `cls_telegraph`,
  `stcn`, `pboc`, `stats_gov`, `fed_fomc`, `us_treasury`,
  `sec_edgar`; plus `rss_fetcher` + optional `newsnow_fetcher`.
- `finpulse_pipeline.ingest` — orchestrates fetchers, deduplicates
  on canonical URL hash, tracks cross-source re-sightings via
  `raw.also_seen_from`, updates `source.{id}.last_ok` /
  `last_error` in config.

### Added — Phase 3 (AI filter)

- `finpulse_ai/filter.py` — two-stage filter
  (`extract_tags` → `score_batch`) reusing `api.get_brain()`;
  `batch_size=10` with per-item graceful-degradation.
- `finpulse_ai/dedupe.py` — canonical URL merge + simhash title
  dedupe (Horizon range), with optional LLM topic clustering gated
  by `dedupe.use_llm` (default off).
- Interest-file SHA256 cache: when `ai_interests` changes all
  `ai_score` rows are nulled so the next cycle re-scores.

### Added — Phase 4 (modes + dispatch + schedule)

- `finpulse_report/render.py` — `build_daily_brief()` that ranks +
  formats articles into markdown and HTML blobs with inline CSS
  mirroring the `avatar-studio` palette.
- `finpulse_pipeline.run_daily_brief` — persists the rendered
  digest into the `digests` table and marks the task succeeded.
- `finpulse_frequency.py` — `+must` / `!exclude` / `@alias`
  / `[GLOBAL_FILTER]` DSL compiler and matcher with the deepcopy +
  size-bound hardenings in §13.2 of the plan.
- `finpulse_pipeline.evaluate_radar` + `run_hot_radar` — radar
  evaluation over the articles index + per-target broadcast
  through `DispatchService`.
- `finpulse_notification/splitter.py` — line-boundary splitter
  with `base_header` prepend + oversize-line force split
  (fix for lost-headline truncation in long pushes).
- `finpulse_dispatch.py` — thin wrapper over `api.send_message`
  with per-key cooldown, content-hash dedupe, and inter-chunk
  pacing; `broadcast()` fans out to multiple `(channel, chat_id)`
  targets.
- `on_schedule` hook + `_is_finpulse_schedule` match predicate so
  the host `TaskScheduler` invokes fin-pulse only for tasks whose
  name starts with `fin-pulse:`.
- `/schedules` REST triad (`GET` / `POST` / `DELETE`) that
  creates `ScheduledTask.create_cron(silent=True)` so the host
  does not duplicate fin-pulse's own IM payloads.
- `/available-channels` REST route that enumerates the host
  gateway adapters with a graceful probe fallback.

### Added — Phase 5 (agent tools)

- `finpulse_services/query.py` — shared query service used by both
  the REST router and the seven agent tools. `_clamp` /
  `_clamp_float` guard against misbehaving LLM
  payloads cannot hand in `limit=99999`.
- `plugin._handle_tool` — async dispatch through
  `build_tool_dispatch()` so REST and tool surfaces stay lockstep.
- JSON serialisation helper with `default=str` fallback so
  exotic payloads never crash the Brain adapter.
- 26 new service tests covering clamp edge cases, redaction,
  settings CRUD, search filters, create-path validation for all
  three modes, and dispatch table coverage.

### Added — Phase 6 (UI polish + docs)

- 5-tab UI hydrated against the live REST surface:
  **Today** (source / window / min_score filters + copy +
  one-click ingest), **Digests** (generate + iframe preview +
  resend), **Radar** (rule editor + dry run + save), **Ask** (7
  tool cards with JSON samples + "copy natural-language prompt"),
  **Settings** (source health, channels with per-adapter test,
  schedule CRUD, NewsNow 3-stage wizard, LLM hint card).
- NewsNow 3-stage wizard (`off` / `public` / `self_host`) with
  public-service warning banner and self-host docker recipe.
- `tests/test_smoke.py::test_ui_tabs_are_hydrated` — regression
  guard that every hot-path REST call lives in `index.html`.
- Five docs at the plugin root: `README.md`, `SKILL.md`,
  `USER_TEST_CASES.md`, `CHANGELOG.md` (this file), `VALIDATION.md`.

### Notes

- Python 3.11+ required at runtime (host uses `StrEnum`).
- The FastMCP stdio entry was **explicitly deferred to V1.1**;
  V1.0 exposes tools through the host `register_tools` single
  track only.
- Test matrix: 213 passed + 4 skipped (intentional: live-network
  fetchers and optional `feedparser`).
