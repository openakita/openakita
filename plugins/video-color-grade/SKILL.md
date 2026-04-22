# Skill — `video-color-grade`

A subtle, ±8%-clamped one-click color grade for any video.  Samples
brightness / contrast / saturation via ``ffmpeg signalstats`` and emits
a per-clip ``eq=...`` filter.  Falls back to one of the named presets
(``subtle`` / ``neutral_punch`` / ``warm_cinematic``) when the user
explicitly asks or when sampling fails.

This plugin is a thin wrapper around the SDK helpers
:func:`openakita_plugin_sdk.contrib.sample_signalstats` and
:func:`openakita_plugin_sdk.contrib.auto_color_grade_filter` — keep all
algorithmic changes there, never inline new heuristics here.

## When to use

| Scenario | Recommended mode |
|---|---|
| User uploaded a flat / dark phone clip and wants it to "look clean" | `auto` (default) |
| User wants a creative retro look | `preset:warm_cinematic` |
| User wants a barely-perceptible cleanup floor | `preset:subtle` |
| User wants no grade at all (just a normalized re-encode) | `preset:none` |

## Tools the brain can call

* `video_color_grade_create(input_path, mode?, output_path?)`
* `video_color_grade_preview(input_path, mode?)` — analyze without rendering
* `video_color_grade_status(task_id)`
* `video_color_grade_list()`
* `video_color_grade_cancel(task_id)`

## HTTP routes

* `GET /healthz`
* `GET/POST /config`
* `POST /preview` — return plan + ffmpeg argv (no render)
* `POST /tasks` — queue a render
* `GET /tasks` / `GET /tasks/{id}` / `DELETE /tasks/{id}`
* `POST /tasks/{id}/cancel`
* `GET /tasks/{id}/video` — download the graded mp4

## Quality Gates

| Gate | What it checks | When | Pass criterion |
|------|---------------|------|----------------|
| G1 输入完整性 | `input_path` is non-empty | route entry | `QualityGates.check_input_integrity` returns non-blocking |
| G2 输出 schema | `result.output_path` ends in `.mp4` and exists on disk | after worker run | `Path(output_path).is_file()` |
| G3 调色边界 | All eq adjustments within `±DEFAULT_GRADE_CLAMP_PCT` of 1.0 | inside engine | enforced by `auto_color_grade_filter` clamp |

## Failure modes the user might see

* **"no measurable adjustment was needed"** — the source is already
  well-balanced; output is essentially a re-encode.  Surfaced as a
  yellow `Verification` flag in `result.verification`.
* **"signalstats produced no usable samples"** — ffmpeg analysis
  failed; the plugin fell back to the `subtle` preset.  Yellow flag.
* **"ffprobe could not read the source duration"** — the file may have
  a corrupt MOOV atom; the render still succeeded but the user should
  verify visually.
