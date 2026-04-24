# Skill — Footage Gate

> Status: skeleton (Phase 0). The 10-section operator manual lands in Phase 6.

## What it is

Footage Gate is a four-mode local post-production quality gate. Pick a mode,
upload your source / cut, and the plugin runs the matching FFmpeg pipeline
end-to-end without calling any external API. Output is written under the
plugin's data directory and surfaced through the in-page Tasks tab.

## When to use which mode

- **source_review** — _before_ you start editing: get a one-shot risk
  report on the raw clips (resolution, audio channels, length, optional
  speech transcription).
- **silence_cut** — when a long take has obvious dead air; trims and
  concatenates the non-silent intervals with configurable padding.
- **auto_color** — when a finished cut feels flat / muddy; samples 10
  frames, derives a conservative `eq` chain (contrast / gamma / saturation,
  each clamped to ±8 %), and re-renders.
- **cut_qc** — _after_ you've exported the master: verifies cut boundaries,
  audio spikes, subtitle overlay safe zones, and total duration vs the EDL.
  When the optional **auto-remux** toggle is on (per-task in the UI), the
  plugin will retry the export up to 3 times if QC flags a fixable issue.

## Constraints

- FFmpeg ≥ 4.4 with `signalstats`, `eq`, `subtitles`, and one of
  (`tonemap` | `zscale + tonemap_zscale`) compiled in. The Settings page
  installs and verifies this.
- No LLM / cloud calls in v1.0 — the plugin is fully offline.
