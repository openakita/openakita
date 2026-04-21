# dub-it — Skill

Translate a video's audio into another language and dub it back
over the original picture.  Pre-flights with the SDK's
`source_review` (D2.3) so bad inputs (240p screen recordings,
1.5-second clips, audio-only files, ...) are rejected *before* any
quota is spent.

## When to invoke

- The user has a video and wants the same picture but with a
  re-voiced track in another language ("把这个 60 秒英文教程配
  成中文，原音保留低音").
- A pipeline needs to localise existing video content for a
  multi-language launch.

Do **not** invoke for transcription-only ("我只想要字幕") — call
`transcribe-archive` instead.

## Brain tools

| Tool | Use |
|------|-----|
| `dub_it_create` | Submit a dub job (async). |
| `dub_it_status` | Status + segment summary + error message. |
| `dub_it_list` | Recent jobs. |
| `dub_it_cancel` | Cancel a running job. |
| `dub_it_review_source` | Run `source_review` *only* — surfaces "will this even work?" before paying for quota. |
| `dub_it_check_deps` | Check ffmpeg / ffprobe presence on PATH. |

## HTTP routes

`GET /healthz`, `GET /check-deps`, `GET /config`, `POST /config`,
`POST /review`, `POST /tasks`, `GET /tasks`, `GET /tasks/{id}`,
`POST /tasks/{id}/cancel`, `DELETE /tasks/{id}`,
`GET /tasks/{id}/output`.

`POST /tasks` body:

```json
{
  "source_video": "/abs/path/to/in.mp4",
  "target_language": "zh-CN",
  "output_path": "/abs/path/to/out.mp4",
  "duck_db": -18,
  "keep_original_audio": true,
  "source_language_hint": "en"
}
```

## Pipeline (D2.3 source_review → 5 stages)

1. **review** — `review_video()` (resolution, duration, fps, codec).
   Hard errors short-circuit the job; warnings flow into D2.10
   verification.
2. **extract** — `ffmpeg -vn -ac 1 -ar 16000 -c:a pcm_s16le …`
   produces a Whisper-friendly mono WAV.
3. **transcribe** — caller-supplied async transcriber.  Default
   stub returns one segment with the file basename.
4. **translate** — caller-supplied async translator.  Default is
   identity (use when source and target language already match).
5. **synthesize** — caller-supplied async TTS that writes the
   dubbed audio to disk.  Default stub writes 0.1s of silence.
6. **mux** — `ffmpeg` mixes dub onto the original video.  When
   `keep_original_audio=True` the original is ducked by `duck_db`
   and amix'd under the dub; otherwise the original audio is dropped.
7. **verify (D2.10)** — yellow-flag missing segments, empty
   translations, zero-byte outputs, source-review warnings.

## Quality gates

- **G1 input**: `source_video` exists; `target_language` ∈
  ALLOWED_TARGET_LANGUAGES; `output_format` ∈ {mp4, mov, mkv, webm};
  `duck_db` ∈ [-60, 0].
- **G2 source**: `review_video.passed` must be True — bad inputs
  are surfaced as `error_message` and the job fails fast.
- **G3 ffmpeg**: extract argv uses `pcm_s16le` (Whisper-friendly).
  Mux argv copies video stream (`-c:v copy`), re-encodes audio (AAC),
  and uses `-shortest` to avoid trailing-silence runaway.
- **G4 ducking**: `volume={duck_db}dB` + `amix` when keeping
  original audio; the filter graph is identical to the Premiere
  voice-over preset (-18 dB by default).
- **G5 verification (D2.10)**: failure / 0 bytes / 0 segments /
  empty translations / review warnings → flagged.

## Reuse pattern

```python
from plugins.dub_it.dub_engine import (
    plan_dub, run_dub, to_verification, default_translator,
)

plan = plan_dub(
    source_video="in.mp4", target_language="zh-CN",
    output_path="out.mp4", duck_db=-18,
)

result = await run_dub(
    plan,
    transcribe=my_whisper_transcriber,
    translate=my_llm_translator,
    synthesize=my_tts_synthesizer,
    workdir="/tmp/dubwork",
)
print(result.to_dict())
print(to_verification(result).to_dict())
```

## Notes for new contributors

- `default_translator` is *identity* — it copies `text` into
  `translated_text`.  This is the right default when the source
  and target language already match (e.g. re-dubbing a Chinese
  video with a different voice in Chinese).
- `default_synthesize` writes 0.1 s of silence in a real RIFF/WAVE
  container so HTTP smoke tests pass without needing a real TTS.
- The mux uses `amix=duration=longest:dropout_transition=2`; if
  the dub is longer than the original video, ffmpeg's `-shortest`
  flag will trim *both* tracks to the video duration — set it on
  the mux command builder if you want a different policy.
