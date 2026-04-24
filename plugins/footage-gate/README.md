# Footage Gate · 成片质量门

> Final-cut quality gate — four post-production modes powered entirely by
> **local FFmpeg**, with **no LLM/API dependency** in the default path. The
> only optional cloud call is DashScope Paraformer transcription, opt-in via
> a per-task UI toggle and Settings API key.

**Version**: 1.0.0 (2026-04-24)
**Status**: ✅ shipped (4 modes / 16 routes / 5 tools / 195 tests green)
**Plugin ID**: `footage-gate`
**Display name (zh)**: 成片质量门

---

## 1. Overview

Footage Gate is the post-production *gatekeeper* in the OpenAkita plugin
family. It assumes you already have raw footage or an exported master and
need to verify quality before shipping.

| Mode | Catalog ref | What it does |
|------|-------------|--------------|
| `source_review`  | C6 SourceMediaReview | Probe video / audio / image, flag low resolution / mono audio / too-short clips, optionally call Paraformer for a transcript summary. |
| `silence_cut`    | D2 SilenceCutter     | RMS-based silence detection in pure NumPy (no `aubio`!), morphological merge of non-silent intervals, FFmpeg concat output. |
| `auto_color`     | C1 AutoColorGrade    | Sample 10 frames with `signalstats`, derive an `eq` filter chain (contrast / gamma / sat) clamped to ±8 %, prepend an HDR→SDR `tonemap` chain when the source is HLG/PQ. |
| `cut_qc`         | C2 CutBoundaryQC     | 4 checks (boundary frame jitter / waveform spike / subtitle vertical safe zone / EDL duration consistency), with an optional **auto-remux** loop (≤3 attempts, **toggle exposed in the UI per task**). |

Every mode runs entirely on the user's machine. The only "online"
operation is the optional Paraformer transcription in `source_review`,
which is OFF by default and requires an explicit API key in Settings.

## 2. Architecture

```
plugin.py                 ← FastAPI router + 5 AI tools + 7-step on_load
├─ footage_gate_models.py        ← MODE_IDS / ERROR_HINTS / TONEMAP_CHAIN
├─ footage_gate_task_manager.py  ← SQLite tasks + config + assets_bus (v2.0 reserved)
├─ footage_gate_ffmpeg.py        ← ffprobe / extract_frames / extract_pcm_mono / is_hdr_source
├─ footage_gate_silence.py       ← pure-NumPy non-silent interval detection
├─ footage_gate_grade.py         ← signalstats sampling → eq + tonemap chain
├─ footage_gate_review.py        ← source media review + risk thresholds
├─ footage_gate_qc.py            ← boundary / spike / subtitle / duration + auto-remux loop
├─ footage_gate_pipeline.py      ← 8-step orchestrator (setup → finalize → handoff)
├─ footage_gate_inline/          ← vendored host-side helpers (system_deps / upload_preview / storage_stats)
├─ ui/dist/index.html            ← single-bundle React UI (≤2800 lines, 100 % self-contained)
├─ tests/                        ← 195 unit + smoke tests (no network, no FFmpeg required)
└─ VALIDATION.md                 ← 8 upstream-defects defenses with citations
```

### Data flow per task

```
UI    →  POST /tasks  →  TaskManager.create(pending)
                    ↓
              spawn_task → run_pipeline (executor)
                    ↓
       setup_environment → validate_input → prepare_assets
                    ↓
           dispatch_by_mode  ← per-mode sub-pipeline
                    ↓
               emit_progress (UI event)
                    ↓
                  finalize  → TaskManager.update(done | failed)
                    ↓
              broadcast_ui_event("task_update")
```

## 3. Install / Run

This plugin ships with the standard `OpenAkita` host. After enabling it
under **Settings → Plugins**:

1. Open the plugin tab. The header shows a `FFmpeg ready vX.Y.Z` pill.
   If it says `FFmpeg not installed`, click the orange banner → **设置 →
   系统依赖 → 一键安装**. The installer supports `winget`, `brew`,
   `apt`, `dnf`, `pacman`, and a manual download fallback.
2. (Optional) Open **设置 → 转写 API**, paste your DashScope API key
   and click **保存**. This is only needed if you want
   `source_review` to call Paraformer.
3. Verify under **设置 → 权限** that all `data.own` / `routes.register`
   / `tools.register` permissions are green. If any are missing, click
   **一键授予全部**.

## 4. UI walkthrough

### Tabs

- **创建任务** — pick a mode → upload your file → set params → click
  **创建任务**. A modal confirms the task started.
- **任务列表** — left rail shows tasks (filterable by status + mode);
  right rail shows the selected task's details, output / report path
  buttons, and any QC issues / error hints.
- **使用说明** — collapsible cheat sheet with mode descriptions, the
  recommended workflow, tips, and the upstream-defects summary.
- **设置** — 6 sections (transcription API / permissions / FFmpeg
  installer / defaults / storage / about), 1:1 aligned with
  `seedance-video`'s settings layout.

### Per-mode toggles worth highlighting

- **auto_color → HDR → SDR tone-map** — defaults to ON. Auto-detects
  HLG/PQ source and surfaces a stronger warning when detected. This
  defends against `video-use` PR #6 (HDR clipping) — see VALIDATION.md.
- **cut_qc → 自动重渲修复** — defaults to OFF (per the user's explicit
  requirement). Only when ON does the **最大重渲次数** field appear
  (range 1–3). Each attempt re-encodes via FFmpeg and re-runs the 4 QC
  checks; the loop stops on the first attempt that yields zero issues.
- **source_review → 调用转写摘要** — OFF by default; turning it ON
  without an API key in Settings yields a clean error_kind=`config`
  task with hints rather than a 500.

## 5. AI tools

```python
footage_gate_create     # mode + input_path + params  → task id
footage_gate_status     # task_id                     → status JSON
footage_gate_list       # mode/status/limit           → list of tasks
footage_gate_cancel     # task_id                     → cancel pending/running
footage_gate_settings_get  # ()                       → config snapshot
```

These hit the same code path as the REST routes so there is no API skew
between agent-driven and UI-driven runs.

## 6. Defensive measures vs upstream

This plugin proactively defends against **8 real upstream issues / PRs**
across the three reference repositories (`video-use`, `CutClaw`,
`OpenMontage`). The full audit is in [`VALIDATION.md`](VALIDATION.md);
TL;DR:

| # | Upstream | Pitfall | Defense |
|---|----------|---------|---------|
| 1 | video-use PR #6 | HDR sources clip when graded | `TONEMAP_CHAIN` prepended whenever `is_hdr_source(...)` |
| 2 | video-use PR #5 | Vertical subtitles overlap UI elements | `MIN_SUBTITLE_MARGINV_VERTICAL = 90` enforced in subtitle_overlay_check |
| 3 | OpenMontage PR #46 | Removed `tool_registry` API broke imports | Direct subprocess to ffprobe/ffmpeg, no `tool_registry` import |
| 4 | CutClaw issue #3 | `aubio` install fails on Win/macOS | Pure-NumPy silence detection, no `aubio` / `madmom` / `librosa` |
| 5 | OpenMontage issue #43 | EDL field naming is inconsistent | `parse_edl` accepts both `in_seconds`/`out_seconds` and `start_seconds`/`end_seconds` |
| 6 | OpenMontage issue #42 | Mixed video+image cuts crash concat | `preprocess_image_cuts` converts image rows to short MP4 loops |
| 7 | upstream code reviews | `usable_for` field sometimes missing | `source_review` always emits `usable_for` + risk reasons |
| 8 | toolchain hygiene | Drift on FFmpeg minimum version | Settings page shows version + we require ≥ 4.4 in docs / installer |

## 7. User 5-minute smoke test

Use this as a one-pass acceptance checklist after installing or upgrading.

1. **FFmpeg** — open the plugin. Header pill shows
   `FFmpeg ready X.Y.Z`. If not, install via Settings.
2. **source_review** — pick the mode, upload any short MP4
   (≥ 1 s, ≤ 100 MB). Wait for **done**. The task detail shows
   `duration_input_sec`, `is_hdr_source`, and a report path you can
   open from the right rail.
3. **silence_cut** — same MP4. Wait for **done**. The task detail shows
   `removed_seconds` and an output path you can open. Output runs
   without dead air.
4. **auto_color** — same MP4 with HDR toggle ON. Wait for **done**. The
   output looks visually identical or slightly punchier (no banding /
   crushed blacks) and the report path lists the chosen `eq` values.
5. **cut_qc** — paste a 1-line EDL JSON `{"cuts":[{"in_seconds":0,"out_seconds":2}]}`,
   leave auto-remux OFF, run. Task completes; if the input is shorter
   than 2 s you should see a `duration` issue.
6. **Auto-remux** — turn auto-remux ON, retry. Either zero issues (no
   remux) or up to 3 remux attempts; verify `qc_attempts ≤ 3`.

If all six steps pass, the plugin is ready for production use.

## 8. Reference

- Implementation plan: `plans/footage-gate v1.0`
- Catalog reference: `findings/plugin_atoms_catalog.md`
- Roadmap entry: `docs/post-production-plugins-roadmap.md`
- Upstream audit: [`VALIDATION.md`](VALIDATION.md)
- Operator manual: [`SKILL.md`](SKILL.md)
- Test matrix: [`USER_TEST_CASES.md`](USER_TEST_CASES.md)
- Changelog: [`CHANGELOG.md`](CHANGELOG.md)
