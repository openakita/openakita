# Skill: ppt-to-video

Turn a `.pptx` (or `.ppt` / `.odp`) into a narrated MP4 in one shot.

## When to invoke

The user wants to *narrate a slide deck without filming themselves*:

- "把这个 PPT 转成讲解视频"
- "PPT 配音生成视频"
- "convert this presentation into a YouTube video"
- "voice-over my slides automatically"

Skip this plugin and use a different one when:

- The user wants screen-recording with a cursor (use the upcoming
  `screen-recorder` plugin, not this one).
- The user already recorded narration and just needs cuts (use
  `bgm-mixer` to mix BGM with their existing voice track).
- The user wants slide animations preserved as video (LibreOffice's
  PNG export rasterizes the final frame; animated transitions are
  flattened — flag this in the response if the deck relies on motion).

## Brain tools

| Tool | What it does |
|------|--------------|
| `ppt_to_video_create` | Queue a deck → MP4 job; returns `task_id` |
| `ppt_to_video_status` | Check status / error message of a job |
| `ppt_to_video_list` | List up to 20 recent jobs |
| `ppt_to_video_cancel` | Cancel a running job |
| `ppt_to_video_check_deps` | Print whether soffice + python-pptx + ffmpeg are ready |

Always run `ppt_to_video_check_deps` first the very first time on a
machine — the plugin needs `soffice` (LibreOffice) and `python-pptx`
to be installed, neither of which the SDK can pull in automatically.

## HTTP routes

| Verb + path | Purpose |
|-------------|---------|
| `GET  /healthz` | Liveness + dep status |
| `GET  /check-deps` | Dep status + install hints |
| `GET  /config` | Read default voice / TTS provider / silent gap |
| `POST /config` | Override defaults |
| `POST /preview` | Validate input + return planned output path (no rendering) |
| `POST /tasks` | Queue a new job |
| `GET  /tasks` | List jobs (filterable by `?status=`) |
| `GET  /tasks/{id}` | Inspect one job |
| `POST /tasks/{id}/cancel` | Cancel a running job |
| `DELETE /tasks/{id}` | Drop the record |
| `GET  /tasks/{id}/video` | Download the rendered MP4 |
| `POST /upload-preview` | (from SDK `add_upload_preview_route`) preview uploaded files |

## Voice-over pipeline

1. **Slides → PNGs.** `soffice --headless --convert-to png` exports
   one PNG per slide.
2. **Notes extraction.** `python-pptx` walks each slide's notes
   placeholder. Empty notes get a configurable silent gap
   (`default_silent_slide_sec`, default 2.0s).
3. **TTS.** Each non-empty note string is passed to the chosen TTS
   provider (re-uses `plugins/avatar-speaker/providers.py` —
   edge-tts / DashScope CosyVoice / OpenAI / `stub`). When a slide
   throws inside the provider we *fall back to silence* rather than
   killing the whole job — failures are surfaced through
   `tts_fallbacks` in the verification envelope.
4. **Per-slide clip.** Each `(image, audio)` pair becomes a short
   `libx264 + AAC` clip, padded to even dimensions so vertical decks
   don't blow up the encoder. Slides without audio get an
   `anullsrc` silent track so the concat demuxer doesn't choke on
   missing audio streams.
5. **Concat.** All clips are stitched with the ffmpeg concat
   demuxer (`-c copy`); runtime stays linear in the slide count.

## Quality gates

| Gate | What it checks | When | Pass criterion |
|------|----------------|------|----------------|
| G1 source review | input file exists + extension is `.pptx`/`.ppt`/`.odp` | inside `plan_video` | hard-fails the request when wrong |
| G2 dep check | soffice + python-pptx + ffmpeg discoverable | inside `_check_deps` | yellow flag when any missing — actionable hints in `*_install_hint` fields |
| G3 narration coverage | `>50%` of slides have non-empty notes **and** TTS does not silently fall back | inside `to_verification` | yellow flag when violated |
| G4 output integrity | final MP4 size > 0 | inside `to_verification` | yellow flag when zero (concat probably failed) |
| G5 stub provider warning | TTS provider == `"stub"` | inside `to_verification` | yellow flag — output is silent placeholder |

## Failure modes & coach hints

| Symptom | Cause | Hint surfaced via ErrorCoach |
|---------|-------|------------------------------|
| `LibreOffice (soffice) was not found` | soffice not installed | "Install LibreOffice (https://www.libreoffice.org)" |
| `python-pptx is not installed` | python-pptx missing | "pip install python-pptx" |
| `unsupported input extension` | user supplied a `.docx` / `.pdf` | lists the 3 supported extensions |
| `LibreOffice did not produce any PNGs` | empty / corrupt deck | "the source file may be empty or corrupted" |
| `output_path must end in .mp4` | user requested `.mov` / `.mkv` | hint says ".mp4 only — D5 currently doesn't transcode" |
| Final mp4 0 bytes | ffmpeg concat silently failed | yellow flag in verification |

## Reuse

- The `slide_engine` module is intentionally framework-free
  (no FastAPI, no asyncio). The future `shorts-batch` (D3, Sprint 17)
  plugin imports it directly when the LLM decides to assemble a
  slide-style explainer.
- TTS goes through `avatar-speaker/providers.py` — adding a new
  TTS vendor there automatically benefits this plugin.

## Notes for new contributors

- LibreOffice's PNG output naming changes across versions:
  single-slide → `<basename>.png`; multi-slide →
  `<basename>-<N>.png`. `discover_exported_pngs` tolerates both;
  changing it requires updating the corresponding test fixtures.
- `silent_slide_sec` is clamped to `[0.5, 30.0]` so a typo doesn't
  produce a 6-hour video out of one blank slide.
- `output_path` MUST end in `.mp4`. We deliberately don't transcode
  to other containers in this plugin — use `bgm-mixer` for further
  audio mixing or run a separate ffmpeg job for `.mov` / `.webm`.
