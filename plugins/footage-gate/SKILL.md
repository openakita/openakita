---
name: footage-gate
description: Review source media, cut silence, auto color grade, and run cut-boundary QC through Footage Gate.
risk_class: mutating_scoped
---

# Skill — Footage Gate · 成片质量门

> Operator manual for the `footage-gate` plugin (v1.0.0). Use this when you
> are running the plugin in production or coaching another agent through
> the workflow. For source-code architecture see `README.md` §2.

## 1. What it is

A four-mode local post-production quality gate. Each mode is a fully
deterministic FFmpeg pipeline plus a thin Python wrapper that surfaces
risks, fixes, or both. There is no LLM involvement in the default path —
the only optional cloud call is a DashScope Paraformer transcription
under `source_review`.

The plugin owns its own SQLite (tasks + config + assets_bus reserved
for v2.0), its own FFmpeg installer panel (1:1 reused from
`seedance-video`), and its own React UI (single-bundle, ≤2800 lines,
8 hard contracts aligned with `tongyi-image`).

## 2. When to use which mode

| Situation | Mode | Why |
|-----------|------|-----|
| You just imported the day's raw footage and want a triage report. | `source_review` | Catches resolution / mono-audio / too-short clips before you start editing. |
| A long take has obvious dead air you want trimmed. | `silence_cut` | Pure-NumPy RMS detection, no `aubio` install pain. |
| A finished cut feels flat / too dim. | `auto_color` | Sub-second sampling → ±8 % clamped grade. HDR sources auto tone-mapped. |
| You exported the master and want to verify QC before delivery. | `cut_qc` | 4 checks vs your EDL, with optional auto-remux loop (≤3). |

Combine them as the recommended workflow:
`source_review` → `silence_cut` → `auto_color` → `cut_qc`. Each step is
its own task and its output is the next step's input.

## 3. Inputs and outputs

| Mode | Accepted inputs | Output artifacts |
|------|-----------------|------------------|
| `source_review` | mp4 / mov / wav / mp3 / jpg / png | `report.json` (risks, ffprobe metadata, optional transcript), 4 representative frames |
| `silence_cut`   | mp4 / mov / wav / mp3            | `output.mp4` (or `.wav`), `report.json` (intervals removed) |
| `auto_color`    | mp4 / mov                        | `graded.mp4`, `report.json` (chosen eq values, HDR flag) |
| `cut_qc`        | mp4 / mov + EDL JSON             | `report.json` (issue list per check), optional `remux_N.mp4` for each attempt |

EDL JSON canonical shape (cut_qc):

```json
{
  "cuts": [
    {"source": "shot_a.mp4", "in_seconds": 0.0,   "out_seconds": 2.0},
    {"source": "shot_b.png", "in_seconds": 0.0,   "out_seconds": 1.5},
    {"source": "shot_c.mp4", "start_seconds": 5.0, "end_seconds": 7.5}
  ]
}
```

The `start_seconds`/`end_seconds` aliases are accepted to defend against
OpenMontage issue #43.

## 4. Per-task params (UI ↔ API contract)

`source_review`:
- `transcribe: bool` (default `false`) — call Paraformer if API key set.

`silence_cut`:
- `threshold_db: float` (default `-45`) — silence threshold in dB.
- `min_silence_len: float` (s, default `0.15`)
- `min_sound_len: float` (s, default `0.05`)
- `pad: float` (s, default `0.05`) — buffer kept around each non-silent run.

`auto_color`:
- `preset: "auto" | "subtle" | "cinematic"` (default `auto`; `cinematic` is reserved for v1.1).
- `hdr_tonemap: bool` (default `true`) — auto-prepend the `TONEMAP_CHAIN` filter.

`cut_qc`:
- `edl: object` — required. See §3 for shape.
- `auto_remux: bool` (default `false`, **per the user's explicit
  requirement the UI exposes this toggle**).
- `max_attempts: int` (default `3`, range 1–3) — only honoured when
  `auto_remux` is true.

## 5. Status lifecycle

```
pending  →  running  →  done | failed | cancelled
```

A task in `running` can be cancelled from the Tasks tab; it transitions
to `cancelled` and its work directory is left intact for inspection. A
task in `failed` shows `error_kind` (one of `dependency`, `config`,
`input`, `runtime`, `cancelled`, `unknown`) and `error_hints` (a list of
zh strings — the UI also reads `en` if locale is en).

## 6. Errors you might see

| `error_kind` | Typical cause | Fix |
|--------------|---------------|-----|
| `dependency` | FFmpeg / FFprobe missing or too old | Settings → 系统依赖 → 一键安装 |
| `config`     | Paraformer asked but no API key      | Settings → 转写 API → 保存 |
| `input`      | Unsupported codec / corrupt file     | Re-export the source via Premiere / DaVinci to standard h264 / aac |
| `runtime`    | FFmpeg returned non-zero mid-pipeline | Open the report; the stderr tail is included |
| `cancelled`  | User cancelled the task              | Re-run via the Retry button |
| `unknown`    | Anything else                        | File an issue with the task ID — we attach the full traceback |

## 7. Storage & cleanup

- `data/footage_gate/uploads/{videos,audios,images,other}/` — raw uploads.
- `data/footage_gate/tasks/<task_id>/` — per-task work + outputs.
- Settings → 存储 shows per-dir size + opens in OS file explorer.
- Settings → 默认参数 → 任务保留天数 controls the auto-cleanup
  cadence (defaults to 30 days).

## 8. Permissions

The plugin requests the standard Setup-Center permission set:

```
tools.register      # 5 AI tools
routes.register     # 16 REST routes
hooks.basic         # plugin lifecycle
config.read         # read host config
config.write        # write per-plugin settings
data.own            # exclusive access to data/footage_gate/
```

Plus UI permissions: `upload`, `download`, `notifications`, `theme`,
`clipboard`. The Permissions panel in Settings highlights any missing
ones with a one-click grant.

## 9. Known limitations (v1.0)

- `cut_qc` boundary frame check is histogram-diff-based. Hard-edged
  whip-pans may register as false positives — for now, lower the
  threshold via params or run with auto-remux OFF and accept the report.
- `auto_color` only emits a single global `eq` chain. Scene-aware
  grading is on the v1.1 roadmap.
- `assets_bus` table is created but not written. Cross-plugin handoff
  routes (`/handoff/from/{plugin_id}` etc.) land in v2.0 — see
  `docs/post-production-plugins-roadmap.md`.

## 10. Cheatsheet

```bash
# Probe a video locally
ffprobe -v error -show_entries stream=width,height,r_frame_rate -of json IN.mp4

# Reproduce silence_cut without the plugin
ffmpeg -i IN.mp4 -af silenceremove=stop_periods=-1:stop_threshold=-45dB OUT.mp4

# Reproduce auto_color (subtle preset)
ffmpeg -i IN.mp4 -vf "eq=contrast=1.05:gamma=1.02:saturation=1.04" OUT.mp4

# HDR → SDR tone-map (prepended automatically when source is HLG / PQ)
ffmpeg -i IN_HDR.mp4 -vf "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p" OUT_SDR.mp4
```

When in doubt, mirror the params in the Tasks → details JSON and pipe
them through these CLI commands to verify behaviour outside the plugin.
