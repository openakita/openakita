# Skill — `video-bg-remove`

Robust Video Matting (RVM, MobileNetV3) wrapped as an OpenAkita plugin.
Removes the background from any video via an `onnxruntime`-driven
recurrent matting net, then composites the foreground onto a flat
color, a still image, or transparent (RGBA `.mov`).

This plugin is **the only first-party consumer** of the RVM model;
all matting math lives in `matting_engine.py` so `shorts-batch` (D3,
future Sprint 17) can `from matting_engine import run_matting`
directly without wiring HTTP routes.

## When to use

| Scenario | Background |
|---|---|
| User wants chroma-key style green output for re-keying in DaVinci | `color` (default `0,177,64`) |
| User wants to drop a person onto a still photo | `image` |
| User wants an alpha channel for downstream composition | `transparent` (forces `.mov`) |

## Tools the brain can call

* `video_bg_remove_create(input_path, output_path?, background?)`
* `video_bg_remove_status(task_id)`
* `video_bg_remove_list()`
* `video_bg_remove_cancel(task_id)`
* `video_bg_remove_check_deps()` — surfaces onnxruntime / ffmpeg / model status

## HTTP routes

* `GET /healthz` / `GET /check-deps`
* `GET/POST /config`
* `POST /upload-background` — upload an image to use with `background.kind="image"`
* `POST /preview` — build a `MattingPlan` without running RVM
* `POST /tasks` / `GET /tasks` / `GET /tasks/{id}` / `DELETE /tasks/{id}`
* `POST /tasks/{id}/cancel`
* `GET /tasks/{id}/video` — download the matted file

## Quality Gates

| Gate | What it checks | When | Pass criterion |
|------|---------------|------|----------------|
| G1 输入完整性 | `input_path` is non-empty | route entry | `QualityGates.check_input_integrity` returns non-blocking |
| G2 透明输出容器 | `transparent` background → `output_path` ends in `.mov` | inside `plan_matting` | raises `ValueError` otherwise (libx264 has no alpha) |
| G3 抠像覆盖 | `mean_alpha` ≥ 1% over the whole render | inside `to_verification` | otherwise yellow flag (model probably missed the subject) |
| G4 帧数完整 | rendered frame count within ±10% of `duration_sec * fps` | inside `to_verification` | otherwise yellow flag (truncated render) |
| G5 输出非零 | non-transparent output file size > 0 bytes | inside `to_verification` | otherwise yellow flag (silent ffmpeg failure) |

## Failure modes the user might see

* **"onnxruntime is not installed"** — `video_bg_remove_check_deps` returns ✗;
  install with `pip install onnxruntime` (CPU) or `onnxruntime-gpu`.
* **"RVM model not found at …"** — surfaced by `check_deps`; download
  `rvm_mobilenetv3_fp32.onnx` from
  [PeterL1n/RobustVideoMatting releases](https://github.com/PeterL1n/RobustVideoMatting/releases)
  and drop it at `data/plugins/video-bg-remove/models/`.
* **"background.kind='transparent' requires an output_path ending in .mov"** —
  pass `output_path` explicitly with `.mov` extension or omit it (the
  plugin auto-picks `.mov` when no `output_path` is given).
* **"mean alpha is below 1%"** — yellow `Verification` flag; the model
  found nothing person-shaped in the frame.  Ship the result but
  prompt the user to verify visually.
