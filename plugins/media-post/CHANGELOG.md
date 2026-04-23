# media-post CHANGELOG

## [0.1.0] — 2026-04-23

First public release. End-to-end post-edit publishing pipeline covering
4 modes, 22 routes, and a 4-tab UI aligned with the `tongyi-image` kit.

### Added

#### Business capabilities (4 modes)

- **`cover_pick`** — ffmpeg `thumbnail` prefilter (30 candidates) →
  Qwen-VL-max 6-axis aesthetic scoring (composition / clarity /
  subject_prominence / emotional_impact / color_harmony /
  branding_friendly) → top-N selection with bbox annotations.
- **`multi_aspect`** — smart 16:9 → 9:16 / 1:1 recompose. Scene cuts via
  ffmpeg `select='gt(scene,0.4)'`, fps=2 frame extraction, Qwen-VL-max
  subject detection, EMA smoothing (`alpha=0.15`), ffmpeg crop expression
  assembly capped at 95 nesting levels (per
  [`VALIDATION.md`](VALIDATION.md) §3), letterbox fallback when the
  subject leaves the frame.
- **`seo_pack`** — 5-platform parallel SEO bundle (TikTok / Bilibili /
  WeChat / Xiaohongshu / YouTube) via Qwen-Plus. One platform failing
  does not poison the others.
- **`chapter_cards`** — Playwright Chromium primary path renders HTML
  templates with custom DSL placeholders (`{{name:type=default}}`),
  parsed with `re` (no `BeautifulSoup4` per red-line §13). Transparently
  falls back to ffmpeg `drawtext` when Playwright is unavailable.

#### Engineering

- `mediapost_models.py` — 4 modes + 5 platforms + 2 aspects + price
  table + 9-key `ERROR_HINTS` (zh + en) + `MediaPostError` exception.
  `estimate_cost` returns a `CostPreview` with warn / danger flags.
- `mediapost_task_manager.py` — `aiosqlite`, **6 tables** (`tasks`,
  `cover_results`, `recompose_outputs`, `seo_results`,
  `chapter_cards_results`, `assets_bus`). `assets_bus` is **schema-only**
  in v1.0 with `origin_plugin_id` / `origin_task_id` reserved for v0.2
  cross-plugin handoff. Strict `_UPDATABLE_COLUMNS` allowlist guards
  every UPDATE.
- `mediapost_vlm_client.py` — three call surfaces ported from
  CutClaw `Reviewer.py:545-737`: `call_vlm_batch` (single 8-frame batch,
  JSON code-block stripping, length validation, `gc.collect()` in
  `finally`), `call_vlm_concurrent` (`asyncio.Semaphore`, order-preserving
  flatten, failed slots → `None`), `qwen_plus_call` (text LLM for SEO).
  HTTP / transport errors classify into the canonical 9-key taxonomy with
  exponential backoff retries on 429 / 5xx / transport failures.
- `mediapost_cover_picker.py`, `mediapost_recompose.py`,
  `mediapost_seo_generator.py`, `mediapost_chapter_renderer.py` — one
  module per mode, each surfacing a single `async def` entry point.
- `mediapost_pipeline.py` — `MediaPostContext` dataclass + 8-step
  orchestrator with mode-specific dispatch (`_MODE_RUN_STEP`),
  cooperative cancellation, exception → `MediaPostError` translation,
  cost-approval gate, and `task_update` UI broadcast.
- `plugin.py` — `PluginBase` entry, **22 FastAPI routes**, **4 tools**
  (`media_post_create` / `_status` / `_list` / `_cancel`). All Pydantic
  request schemas use `ConfigDict(extra="forbid")` so unknown fields
  return HTTP 422 (mirrors `subtitle-craft` contract).
- `mediapost_inline/upload_preview.py` + `storage_stats.py` — vendored
  from `clip_sense_inline/` (red-line §13: zero cross-plugin imports,
  zero `sdk.contrib`).

#### UI

- Single-file `ui/dist/index.html` (~1540 lines) — React 18 + Babel CDN,
  4 tabs (`Create` / `Tasks` / `Library` / `Settings`), strict
  `tongyi-image` CSS token whitelist (no `.media-post-*` /
  `.mediapost-*` private prefixes), 5 `.oa-settings-section`, 4
  mode-specific detail panels (`CoverGallery` / `RecomposeViewer` /
  `SeoTabbedPanel` / `ChaptersGallery`), `.oa-config-banner` for missing
  API key, `.oa-cost` warn / danger ramp, `useDraft(mode)` localStorage
  persistence per mode.
- Inline `I18N_DICT` with **~168 keys per locale** across 8 namespaces
  (`mediaPost.modes.*`, `.tabs.*`, `.forms.*`, `.errors.*`, `.settings.*`,
  `.library.*`, `.common.*`, `.cost.*`). `useI18n` hook subscribes to
  `OpenAkitaI18n.onChange` for live locale switching.
- `ui/dist/_assets/` — 5-file UI Kit vendored verbatim from
  `tongyi-image` (`bootstrap.js`, `styles.css`, `icons.js`,
  `markdown-mini.js`, `i18n.js`), zero host-mount, zero new CDN.

#### Tests

- **231 hermetic unit tests** across `test_skeleton.py` (red-line guards
  + `_assets` SHA256 match against `tongyi-image`), `test_models.py`,
  `test_task_manager.py` (6-table schema + `_UPDATABLE_COLUMNS`
  allowlist + `assets_bus` zero-write invariant), `test_vlm_client.py`
  (mock httpx hitting all 9 error kinds), `test_cover_picker.py`,
  `test_recompose.py` (EMA smoothing + crop-depth cap), `test_seo_generator.py`,
  `test_chapter_renderer.py` (DSL parser + drawtext fallback),
  `test_pipeline.py` (4 modes × happy / cancel / failure injection),
  and `test_routes.py` (22 routes + Pydantic 422 paths).
- **2 opt-in integration smokes** under `tests/integration/`
  (`test_qwen_vl_smoke.py`, `test_recompose_smoke.py`) gated on
  `DASHSCOPE_API_KEY` and the `integration` marker. Total budget < ¥1.5.

#### Documentation

- `README.md` (10 sections, includes the §7 5-minute manual smoke).
- `SKILL.md` (10 sections — trigger / commands / I/O schemas / error
  codes / decision tree / cost / templates / tests / limits).
- `VALIDATION.md` — empirical Phase 2a findings: VLM 8-frame ordering
  reliability, ffmpeg `select='gt(scene,0.4)'` sanity, ffmpeg crop
  expression nesting **hard cap at 95** (downsampling strategy in
  `mediapost_recompose._downsample_to_depth_cap`).
- `USER_TEST_CASES.md` (gitignored per repo `.gitignore` line 326,
  4 modes × 3 cases = 12 cases).

### Notes

- **Zero new dependencies** (Python or npm) per
  [`docs/media-post-plan.md` §13](../../docs/media-post-plan.md). Only
  `httpx` / `aiosqlite` / `pydantic` / `fastapi` (already in core), plus
  optional Playwright via lazy import.
- **Zero `from openakita_plugin_sdk.contrib import …`** — SDK 0.7.0
  removed that subpackage.
- **Zero `from _shared import …`** — that's the archive shim.
- **Zero `shell=True`** subprocess calls (every call uses
  `asyncio.create_subprocess_exec`).
- **Zero cross-plugin imports** — common helpers are vendored under
  `mediapost_inline/`.
- `requires.sdk` anchored to `>=0.7.0,<0.8.0`, matching every other
  first-class plugin.

### Deferred to v0.2

- `/handoff/*` routes + cross-plugin UI jump buttons (subtitle-craft →
  media-post and avatar-studio → media-post). The `assets_bus` table
  schema is already in place to receive them.
- `multi_aspect` extra ratios (`3:4`, `21:9`).
- Custom user-supplied SEO YAML overrides.
- Multi-language SEO output (currently zh + en only via the i18n hint
  layer; the prompts themselves are platform-natural).
- Auto chapter detection.
- Direct platform OAuth publish.
- Source video transcoding (out of scope — that's `footage-doctor`).
