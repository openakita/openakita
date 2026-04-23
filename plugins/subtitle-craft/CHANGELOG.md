# Subtitle Craft · CHANGELOG

All notable changes to this plugin live here. This project adheres to
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-04-23

First public release. 4 modes × 21 routes × 4 tools, full UI, integration
tests scaffolded.

### Added — backend

- `subtitle_models.py` — 4 mode definitions, 5 built-in style presets,
  Qwen-MT translation model catalogue, **9-key canonical `ERROR_HINTS`**
  taxonomy aligned 1:1 with `clip-sense` (red-line C2: no `rate_limit`).
- `subtitle_task_manager.py` — 4-table SQLite layer
  (`tasks` / `transcripts` / `assets_bus` / `config`), whitelist-based
  `update_task_safe`, cooperative cancel registry,
  `assets_bus` + `tasks.origin_*` reserved schema (always NULL in v1.0,
  populated by v2.0 with zero migration).
- `subtitle_asr_client.py` — DashScope wrappers:
  - Paraformer-v2 word-level ASR with **POST-only** task query (P0-5
    ruling per `VALIDATION.md §2`; no GET fallback branch).
  - Word-level field normalization (P0-15) — pipeline never sees raw
    `begin_time` / `end_time` / `start_time` field-name variance.
  - Qwen-MT chunked translation (≤8500 chars per chunk per
    `VALIDATION.md §3`), defensive prose-preamble stripping (P1-5/P1-6).
  - Qwen-VL-max character identification with non-fatal fallback (P1-12).
  - 9-canonical `error_kind` taxonomy via `map_vendor_kind_to_error_kind`.
- `subtitle_renderer.py` — SRT/VTT generation, timeline repair, FFmpeg
  ASS burning, Playwright HTML overlay (lazy import per **P0-13/P0-14**;
  singleton-managed Chromium; HTML failure auto-falls back to ASS per
  **P1-13**); FFmpeg path escaping for Windows (P0-16 ruling).
- `subtitle_pipeline.py` — 7-step linear pipeline (`setup_environment`
  → `estimate_cost` → `prepare_assets` → `asr_or_load` → optional
  `identify_characters` step 4.5 → `translate_or_repair` → `render_output`
  → `burn_or_finalize`) with cooperative cancel checks at every step
  boundary, mode-specific `skip_steps`, and SSE event emission for
  every step entry/exit + state transition.
- `plugin.py` — `Plugin(PluginBase)` lifecycle (`on_load` /
  `_async_init` / `on_unload`), 21 FastAPI routes, 4 tools,
  `/healthz` 4-field contract (`ffmpeg_ok`, `playwright_ok`,
  `playwright_browser_ready`, `dashscope_api_key_present`),
  background polling 3-stage backoff (3s → 10s → 30s) for orphan
  reaping, `_PlaywrightSingleton.close()` invoked on `on_unload`.
- 5 vendored helpers under `subtitle_craft_inline/`:
  `vendor_client.py`, `upload_preview.py`, `storage_stats.py`,
  `llm_json_parser.py`, `parallel_executor.py`.

### Added — UI

- `ui/dist/index.html` (~2000 lines, single-file React + Babel CDN,
  self-contained — no host-mounted `/api/plugins/_sdk/*` dependency).
- 4 lazy-mounted tabs: 创建任务 / 任务列表 / 素材库 / 设置.
- 4-mode dispatcher inside Create with mode-specific forms; character
  identification is an **embedded toggle under `auto_subtitle`** (gated
  on `diarization_enabled`, NOT a standalone mode).
- Live SSE updates on `task_update`; 15-second polling fallback.
- tongyi-image 8-item alignment: hero title, config banner,
  section-style labels, lazy-mount tabs, bridge SDK, theme/locale
  follow, `oa-preview-area` right-side preview, modal + toast.
- Full zh-CN + en i18n dictionary registered via `OpenAkitaI18n`.

### Added — tests

- `tests/test_skeleton.py` — vendored imports + red-line grep guards.
- `tests/test_data_layer.py` — 4-table schema, whitelist updates,
  cancel registry, `assets_bus` reserved-NULL invariant.
- `tests/test_renderer.py` — SRT/VTT output, repair edge cases,
  Playwright HTML fallback to ASS.
- `tests/test_pipeline.py` — step-skip matrix per mode, step 4.5
  conditional trigger + non-fatal failure, cache-hit, cooperative
  cancel, SSE shape, canonical `error_kind` enforcement.
- `tests/test_plugin.py` — 21-route registration, `/healthz` 4-field
  contract, no-handoff guards, settings masking, `on_unload`
  Playwright close, 4-tool dispatch, Pydantic `extra="forbid"` rejection.
- `tests/test_ui_smoke.py` — UI grep / structure tests for Phase 5
  Gate 5 (no handoff strings, all 4 tabs/4 modes declared, 8-item
  alignment, char-id gated by diarization, `/healthz` rendering, SSE
  wire, no drawer pattern, no `/_sdk/` host dependency).
- `tests/integration/test_paraformer_smoke.py` — opt-in integration
  smoke that exercises a 30-second sample against the real DashScope
  Paraformer-v2 endpoint when `DASHSCOPE_API_KEY` env var is set.
  CI skips by default.
- 159 unit tests passing (subtitle-craft scope) + opt-in integration.

### Added — docs

- `README.md` — install, modes, routes, error kinds, smoke test recipe.
- `SKILL.md` — 10-section trigger / schema / heuristics / cost / scope.
- `VALIDATION.md` — 5 Phase 2a validations (Paraformer POST/GET
  ruling, word-level field set, Qwen-MT chunk budget, ffmpeg Windows
  path escaping, character ID prompt template).
- `USER_TEST_CASES.md` — 4 modes × 3 acceptance scenarios (12 cases).
- `docs/post-production-plugins-roadmap.md` — subtitle-craft entry
  marked v1.0 ✅ shipped.

### NOT included in v1.0 (reserved for v2.0+)

- **No cross-plugin dispatch surface** — zero `/handoff/*` routes,
  zero `subtitle_craft_handoff_*` tools, zero "send to …" UI buttons.
  `assets_bus` table and `tasks.origin_plugin_id` /
  `tasks.origin_task_id` columns are reserved in the schema (always
  NULL in v1.0). Phase 0 grep guards (`test_no_handoff_*`) verify
  the absence of any literal `handoff` references in production code.
  v2.0 will land routes + UI without any data migration.
- No real-time editing / SRT visual editor (planned for v1.2).
- No batch upload (planned for v1.1).

[1.0.0]: https://github.com/your-org/openakita/releases/tag/subtitle-craft-v1.0.0
