# ppt-to-video

Turn a `.pptx` into a narrated MP4 in one shot.

```
deck.pptx  ──► soffice ──►  slide-1.png ──┐
                            slide-2.png ──┤        ┌─► clip-1.mp4 ┐
                            ...           ├─► TTS ─┤   clip-2.mp4 ├─► ffmpeg concat ─► out.mp4
deck.pptx  ──► python-pptx ─► notes[1..N] ┘        └─► ...        ┘
```

## Design principles

- **Algorithm in `slide_engine.py`, glue in `plugin.py`.** The pure
  engine has no FastAPI / asyncio / sqlite imports; future plugins
  (`shorts-batch` D3) can import it directly.
- **Lazy heavy deps.** `python-pptx` is imported only inside
  `extract_slide_notes`; `soffice` and `ffmpeg` are discovered via
  `shutil.which` / well-known paths so the plugin module imports
  cleanly even on a host that has none of them — `check_deps`
  surfaces actionable install hints.
- **Per-slide failure isolation.** A TTS provider that throws on one
  slide does **not** kill the whole job; that slide gets a silent
  gap and the failure surfaces through `tts_fallbacks` in the
  verification envelope.
- **MP4 only output.** `output_path` must end in `.mp4`. This is
  intentional: re-encoding to `.mov` / `.webm` is a separate concern,
  and locking the container lets the concat demuxer use `-c copy`
  (linear runtime).
- **D2.10 verification envelope** flags zero slides, mostly-empty
  notes, excessive TTS fallbacks, zero output size, and the `stub`
  provider — humans get a yellow badge instead of a silent failure.

## Supported inputs

| Extension | Notes |
|-----------|-------|
| `.pptx`   | Recommended — `python-pptx` reads native notes |
| `.ppt`    | Works through LibreOffice; legacy notes may be partial |
| `.odp`    | LibreOffice native; notes work as-is |

## TTS providers

Reuses the multi-vendor TTS layer from `plugins/avatar-speaker/providers.py`:

| `tts_provider` | Backend | Setup |
|----------------|---------|-------|
| `auto` (default) | EdgeTTS → DashScope → OpenAI → stub fallback chain | nothing for EdgeTTS; or set `DASHSCOPE_API_KEY` / `OPENAI_API_KEY` |
| `edge`     | EdgeTTS (free) | `pip install edge-tts` |
| `dashscope` | DashScope CosyVoice | `DASHSCOPE_API_KEY=...` |
| `openai`   | OpenAI TTS | `OPENAI_API_KEY=...` |
| `stub`     | Silent placeholder | nothing — useful for tests / dry runs (yellow-flagged) |

## Setup (one-time)

```powershell
# 1. LibreOffice (provides soffice)
#    Windows: download from https://www.libreoffice.org
#    macOS:   brew install --cask libreoffice
#    Linux:   apt install libreoffice / dnf install libreoffice

# 2. python-pptx (notes extraction)
pip install python-pptx

# 3. ffmpeg
#    Windows: choco install ffmpeg / download from https://ffmpeg.org
#    macOS:   brew install ffmpeg
#    Linux:   apt install ffmpeg

# 4. Optional — pick a TTS provider for narration
pip install edge-tts            # free, reasonable Chinese voices
$env:DASHSCOPE_API_KEY = "..."  # CosyVoice quality
$env:OPENAI_API_KEY = "..."     # OpenAI gpt-4o-mini-tts
```

Verify everything is wired up:

```bash
curl http://localhost:8000/plugins/ppt-to-video/check-deps
```

## HTTP usage

```bash
# preview — no rendering, just validation + chosen output path
curl -X POST http://localhost:8000/plugins/ppt-to-video/preview \
     -H 'content-type: application/json' \
     -d '{"input_path": "/path/to/deck.pptx"}'

# create job
curl -X POST http://localhost:8000/plugins/ppt-to-video/tasks \
     -H 'content-type: application/json' \
     -d '{"input_path": "/path/to/deck.pptx", "voice": "zh-CN-XiaoxiaoNeural"}'

# poll
curl http://localhost:8000/plugins/ppt-to-video/tasks/<task_id>

# download
curl http://localhost:8000/plugins/ppt-to-video/tasks/<task_id>/video -o out.mp4
```

## Brain tools

| Tool | Args | Effect |
|------|------|--------|
| `ppt_to_video_create` | `{input_path, output_path?, voice?, tts_provider?}` | Queue a job |
| `ppt_to_video_status` | `{task_id}` | Status / error message |
| `ppt_to_video_list` | `{}` | 20 most-recent jobs |
| `ppt_to_video_cancel` | `{task_id}` | Cancel running job |
| `ppt_to_video_check_deps` | `{}` | Soffice / python-pptx / ffmpeg readiness + install hints |

## Configuration

`POST /config` overrides any of:

```json
{
  "default_voice": "zh-CN-XiaoxiaoNeural",
  "default_tts_provider": "auto",
  "default_silent_slide_sec": "2.0",
  "default_fps": "25",
  "default_crf": "20",
  "default_libx264_preset": "fast",
  "render_timeout_sec": "1800"
}
```

## Testing

```bash
pytest plugins/ppt-to-video/
```

The test suite (52 engine + 27 plugin = **79 tests**) injects fakes
for soffice, python-pptx, ffmpeg, and the TTS providers — running
them does NOT require LibreOffice, ffmpeg, or any TTS package.

## Limitations (current Sprint 15 scope)

- No slide-transition animation between clips (just hard cuts).
  Future enhancement: optional `xfade` between clips.
- No subtitle burn-in / SRT sidecar; the speaker notes are *only*
  consumed by TTS. Add `transcribe-archive` to a follow-up step if
  you need SRT.
- TTS rate / pitch / per-slide voice override are not yet exposed
  via the HTTP body (engine supports them — UI iteration deferred to
  the front-end sprint).
