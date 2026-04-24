# Footage Gate · 成片质量门

> Final-cut quality gate — four post-production modes powered entirely by
> local FFmpeg, no LLM/API dependency.

**Status**: Phase 0 skeleton — see `plans/footage-gate v1.0` for the full
implementation roadmap. Phases 1–6 land the data layer, FFmpeg tool layer,
pipeline, plugin entry, UI, and integration tests.

## Modes (v1.0)

| Mode | Catalog | Purpose |
|------|---------|---------|
| `source_review`  | C6 SourceMediaReview | Probe video/audio/image sources, flag low-resolution / mono-audio / too-short clips, optional Paraformer transcription. |
| `silence_cut`    | D2 SilenceCutter     | RMS-based non-silent interval detection + morphological merge + concat. |
| `auto_color`     | C1 AutoColorGrade    | `signalstats` frame sampling → `eq` filter chain (contrast / gamma / sat) clamped to ±8 %. HDR→SDR `tonemap` fallback. |
| `cut_qc`         | C2 CutBoundaryQC     | Boundary frame check, waveform spike, subtitle overlay, duration check. Optional **auto-remux** (≤3 attempts) toggled per task in the UI. |

## Lineage

- UI: 100 % aligned to `plugins/tongyi-image` (8 hard contracts —
  `PluginErrorBoundary` / 4-tab layout / `split-layout` / `mode-btn` /
  `onEvent + setInterval` / `oa-config-banner` / `api-pill` / `I18N_DICT`).
- Settings: 100 % aligned to `plugins/seedance-video` (6 sections —
  Permissions / FFmpeg Installer / Storage / Defaults / About / Debug).
- Atoms vendored from:
  - `video-use/helpers/grade.py` (auto_color, +HDR fix from upstream PR #6)
  - `OpenMontage/lib/source_media_review.py` (source_review, with the
    `tool_registry` API change PR #46 already incorporated)
  - `CutClaw/src/audio/madmom_api.py::_compute_non_silent_intervals`
    (silence_cut — re-implemented in pure ``numpy`` so the buggy
    `aubio` dependency from upstream issue #3 is avoided entirely).

## Quick links

- Implementation plan: `plans/footage-gate v1.0`
- Catalog reference: `findings/plugin_atoms_catalog.md`
- Roadmap entry: `docs/post-production-plugins-roadmap.md`
