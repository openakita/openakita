# Subtitle Craft · CHANGELOG

All notable changes to this plugin live here. This project adheres to
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-04-23

Adds the **AI Hook Picker** mode — a fifth processing mode that runs an
SRT through Qwen-Plus (DashScope chat-completion) using a 3-window
strategy (tail → head → N × random) ported 1:1 from CutClaw's
`Screenwriter_scene_short.py` to pick the strongest opening "hook"
dialogue for short-form video.  v1.1 also lays groundwork for v2.0
cross-plugin dispatch but does **not** ship the dispatch surface itself
(red-line C5 stays in force).

### Added — backend

- `subtitle_hook_picker.py` — pure algorithm module decoupled from the
  LLM transport via an injected `llm_caller`; preserves the CutClaw
  `SELECT_HOOK_DIALOGUE_PROMPT` verbatim and the 0.55 fuzzy threshold +
  2-attempt retry loop (red-lines #2/#3 in the v1.1 plan).
- `subtitle_asr_client.SubtitleAsrClient.call_qwen_plus` — generic
  chat-completion wrapper over the existing `BaseVendorClient.request`
  (no new `openai` SDK dependency); JSON-mode whitelisted for
  `qwen-plus` / `qwen-plus-2025-09-11` / `qwen-max`.
- `subtitle_models.py` — `MODES` grows from 4 → 5 entries with
  `hook_picker` (icon `sparkles`, `requires_api_key=True`,
  `requires_ffmpeg=False`); new `HOOK_PICKER_MODELS` catalogue +
  per-token `PRICE_TABLE` rows for `qwen-plus` + `qwen-max`;
  `estimate_cost()` learns to bill the new mode (Qwen-Plus rate × window
  attempts).  `ERROR_HINTS` extended with hook-specific `format` /
  `unknown` hints (no new `error_kind` — red-line C2 holds).
- `subtitle_pipeline.py` — `_step_asr_or_load` validates the loaded SRT
  has ≥5 cues for `hook_picker`; `_step_render_output` branches into a
  new `_do_hook_pick` that calls `select_hook_dialogue`, writes
  `hook.json` + `hook.srt`, and updates the task row.  Skipped steps
  (`prepare_assets` / `identify_characters` / `translate_or_repair` /
  `burn_or_finalize`) are honoured via the existing `skip_steps` field.
  `SubtitlePipelineContext` gains `hook` + `hook_telemetry` fields.
- `plugin.py` — `CreateTaskBody` extended with 7 hook-specific fields
  (`instruction`, `main_character`, `target_duration_sec`,
  `prompt_window_mode`, `random_window_attempts`, `hook_model`,
  `from_task_id`); `CostPreviewBody` adds `hook_model` +
  `random_window_attempts`; `_create_task_internal` resolves
  `from_task_id` against an upstream task's `output_srt_path`.
  `GET /tasks/{task_id}` now enriches succeeded `hook_picker` tasks
  with `hook` + `hook_telemetry` from `task_dir/hook.json`.  New route
  **`GET /library/hooks`** lists archived hook tasks (route count
  25 → 26).
- `plugin.json` — `version` 1.0.0 → 1.1.0; `provides.tools` adds
  `subtitle.hook_pick`.

### Added — frontend (`ui/dist/index.html`)

- 5th mode tile (`auto_subtitle / translate / repair / burn /
  hook_picker`) with a gold gradient chip + "NEW" corner badge.  Reuses
  the v1.0 `oa-mode-tile` skeleton — no new design language.
- `HookPickerForm`: source picker (upload / from existing task),
  instruction textarea (max 200 chars), main-character input, target
  duration slider (6–30 s), collapsible advanced options
  (window strategy / random attempts / model picker).
- Right preview pane (`oa-preview-card`) extended with hook-mode pills
  (source / target duration / window strategy / model) and a 4-card
  empty-state strip ("Load SRT" / "AI selection" / "Duration check" /
  "Export timecode").
- `HookResultPanel` rendered on top of `TaskDetail` for succeeded
  hook tasks: italic block-quote of the chosen lines, AI reason,
  source range + duration pills, copy-timecode button, two disabled
  next-step buttons (cross-plugin dispatch reserved for v2.0).
- `LibraryTab` gains a 4th sub-tab `hooks` backed by a new `HooksList`
  component (2-column grid, copy timecode, delete cascades through
  `DELETE /tasks/{task_id}`).
- 35 new i18n keys × 2 locales (zh-CN + en) under
  `modes.hook_picker.*` / `hook.*` / `create.summary.hook.*` /
  `create.preview.hook.*` / `create.hookFeature.*` / `library.hooks.*`.

### Added — tests

- `tests/test_hook_picker.py` (~210 lines) — unit coverage for the
  algorithm: helper functions (normalize / similarity / fuzzy match /
  prompt formatter) + 7 `select_hook_dialogue` scenarios with mocked
  LLM (tail-success / head-fallback / random-fallback / total-failure /
  duration-rejection / LLM-exception / prompt-template integrity).
- `tests/test_pipeline_hook.py` (~250 lines) — e2e pipeline coverage
  with mocked ASR client: happy-path writes both `hook.srt` + `hook.json`
  and marks the task succeeded; minimal SRT triggers `format` error;
  all-LLM-fail triggers `unknown` with telemetry preserved; missing
  ASR client without API key triggers `auth`; skipped steps verified.
- `tests/test_data_layer.py` — bumped to assert exactly 5 modes
  (`test_modes_exact_5`) with `hook_picker` flagged
  `requires_api_key=True / requires_ffmpeg=False`.
- `tests/test_plugin.py` — `_EXPECTED_ROUTES` adds
  `("GET", "/library/hooks")` (total 25 → 26); two body-class
  assertions verify the 7 hook fields + their defaults.
- `tests/test_ui_smoke.py` — five-mode assertion, hook component
  presence, and a backend-leakage guard
  (`select_hook_dialogue` etc. must NOT appear in the bundle).
- `tests/integration/test_qwen_plus_hook_smoke.py` — live Qwen-Plus
  smoke (skipped without `DASHSCOPE_API_KEY`); uses the new fixtures.
- `tests/fixtures/sample_short.srt` (50 cues, ~8 min mixed dramatic
  + plain dialogue), `sample_minimal.srt` (3 cues, triggers `format`
  error), `sample_long.srt` (1000 cues, exercises the 24K-char cap).

### Notes

- Zero changes to the existing 4 modes' behaviour; v1.0 task rows
  remain valid (no schema migration).  The new `hook` / `hook_telemetry`
  fields exposed by `GET /tasks/{task_id}` are conditional on
  `mode == "hook_picker"` and best-effort (missing `hook.json` is
  silently ignored).
- Cost: a typical hook pick is ≤2 LLM round-trips (≤¥0.01 at the
  Qwen-Plus rate).  The `random_window_attempts` slider lets users
  trade cost for resilience.
- Red-line check (must hold for every commit): no new Python / npm
  deps, no edits to the prompt wording, the 0.55 fuzzy threshold and
  3-window-with-2-retries strategy stay intact, the algorithm module
  must NOT import `subtitle_asr_client`, and the 9 `error_kind` values
  do not grow.

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
