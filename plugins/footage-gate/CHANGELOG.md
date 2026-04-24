# Changelog — Footage Gate

All notable changes to this plugin are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-04-24

### Added

- 4 post-production modes: `source_review`, `silence_cut`, `auto_color`,
  `cut_qc` — see [`README.md`](README.md) §1 for the per-mode summary.
- 16 REST routes (6 task / 1 upload / 1 file preview / 4 system / 2
  settings / 3 storage) wired through a single FastAPI `APIRouter` and
  registered via `PluginAPI.register_api_routes`.
- 5 AI tools (`footage_gate_create` / `_status` / `_list` / `_cancel` /
  `_settings_get`) wired through `PluginAPI.register_tools` so an
  agent can drive the plugin end-to-end without learning the routes.
- SQLite-backed task manager with a `tasks` table (incl. `is_hdr_source`,
  `qc_attempts`, `qc_issues`, `removed_seconds`, `error_*` columns), a
  `config` table seeded with defaults, and a reserved `assets_bus`
  table for v2.0 cross-plugin handoff.
- Vendored `SystemDepsManager` (1:1 with `subtitle-craft`) for
  one-click FFmpeg install / uninstall + log streaming, exposed
  through `/system/components` and `/system/ffmpeg/{install,uninstall,status}`.
- 100 %-self-contained React UI (`ui/dist/index.html`, ≈ 2 050 lines
  of ≤ 2 800 ceiling) using React 18 + Babel-standalone. Aligned to
  the 8 hard contracts from `tongyi-image` (PluginErrorBoundary, 4
  tabs, split-layout, mode-btn, onEvent + setInterval, oa-config-banner,
  api-pill, single-source `I18N_DICT`).
- 6-section Settings tab aligned 1:1 with `seedance-video`:
  Transcription API / Permissions / FFmpeg installer / Defaults /
  Storage / About.
- **UI toggle for `cut_qc` auto-remux** (per the user's explicit
  requirement) — defaults to OFF; when ON the `max_attempts` field
  appears (range 1–3).
- **UI toggle for `auto_color` HDR → SDR tone-map** — defaults to ON
  and surfaces a stronger warning when an HDR transfer is detected.
- 195 unit + smoke tests (no network, no FFmpeg required) covering
  every module (models / task_manager / ffmpeg / silence / grade /
  review / qc / pipeline / plugin / system_deps / ui).
- 27-test UI smoke suite (`tests/test_ui_smoke.py`) regressing the 8
  hard contracts, the 6 settings sections, the 4 mode IDs, the
  cut_qc auto-remux + auto_color HDR toggles, the 2 800-line ceiling,
  and i18n key coverage.
- [`VALIDATION.md`](VALIDATION.md) documenting the 8 upstream-defect
  defenses with code-line citations and corresponding regression tests.
- [`README.md`](README.md), [`SKILL.md`](SKILL.md),
  [`USER_TEST_CASES.md`](USER_TEST_CASES.md) (4 × 6–8 cases), and this
  CHANGELOG.

### Defended against (upstream regressions)

| # | Upstream | Defense |
|---|----------|---------|
| 1 | `video-use` PR #6 | `TONEMAP_CHAIN` prepended whenever `is_hdr_source(...)` |
| 2 | `video-use` PR #5 | `MIN_SUBTITLE_MARGINV_VERTICAL = 90` enforced |
| 3 | `OpenMontage` PR #46 | No `tool_registry` import — direct subprocess to ffprobe / ffmpeg |
| 4 | `CutClaw` issue #3 | Pure-NumPy silence detection — no `aubio` / `madmom` / `librosa` |
| 5 | `OpenMontage` issue #43 | `parse_edl` accepts both `in_seconds`/`out_seconds` and `start_seconds`/`end_seconds` |
| 6 | `OpenMontage` issue #42 | `preprocess_image_cuts` converts image rows to MP4 loops before concat |
| 7 | upstream code reviews | `source_review` always emits `usable_for` + risk reasons |
| 8 | toolchain hygiene | Settings page surfaces FFmpeg version + we require ≥ 4.4 |

### Explicitly omitted (deferred to v2.0)

- `/handoff/from/{plugin_id}` and `/handoff/to/{plugin_id}` cross-plugin
  routes (the `assets_bus` table and the `tasks.origin_*` fields are
  reserved so v2.0 lands without a data migration).
- "Send to clip-sense / avatar-studio" UI buttons.
- `auto_color` `cinematic` preset (the dropdown shows it as disabled).
- Scene-aware grading (single global `eq` chain only in v1.0).

### Internal

- `FootageGateTaskManager` schema is whitelisted via `_UPDATABLE_COLUMNS`
  and `_JSON_KEYS` to defend against accidental SQL injection through
  the `update_task_safe(...)` kwargs.
- The pipeline runs synchronously in a thread executor so it remains
  unit-testable without an event loop while still being async-friendly
  to the FastAPI route layer.
- Upstream snapshot freeze: `video-use@<commit>`,
  `CutClaw@<commit>`, `OpenMontage@<commit>` (see VALIDATION.md
  §"Upstream snapshot freeze"). Upstream changes are not auto-tracked.

[1.0.0]: https://github.com/calesthio/OpenAkita/releases/tag/footage-gate-v1.0.0
