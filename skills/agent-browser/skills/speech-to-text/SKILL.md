---
name: speech-to-text
description: "Transcribe audio to text with Whisper models via inference.sh CLI. Models: Fast Whisper Large V3, Whisper V3 Large. Capabilities: transcription, translation, multi-language, timestamps. Use for: meeting transcription, subtitles, podcast transcripts, voice notes. Triggers: speech to text, transcription, whisper, audio to text, transcribe audio, voice to text, stt, automatic transcription, subtitles generation, transcribe meeting, audio transcription, whisper ai"
allowed-tools: Bash(infsh *)
---

# Speech-to-Text

Transcribe audio to text via [inference.sh](https://inference.sh) CLI.

![Speech-to-Text](https://cloud.inference.sh/u/4mg21r6ta37mpaz6ktzwtt8krr/01jz025e88nkvw55at1rqtj5t8.png)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

infsh app run infsh/fast-whisper-large-v3 --input '{"audio_url": "https://audio.mp3"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Available Models

| Model | App ID | Best For |
|-------|--------|----------|
| Fast Whisper V3 | `infsh/fast-whisper-large-v3` | Fast transcription |
| Whisper V3 Large | `infsh/whisper-v3-large` | Highest accuracy |

## Examples

### Basic Transcription

```bash
infsh app run infsh/fast-whisper-large-v3 --input '{"audio_url": "https://meeting.mp3"}'
```

### With Timestamps

```bash
infsh app sample infsh/fast-whisper-large-v3 --save input.json

# {
#   "audio_url": "https://podcast.mp3",
#   "timestamps": true
# }

infsh app run infsh/fast-whisper-large-v3 --input input.json
```

### Translation (to English)

```bash
infsh app run infsh/whisper-v3-large --input '{
  "audio_url": "https://french-audio.mp3",
  "task": "translate"
}'
```

### From Video

```bash
# Extract audio from video first
infsh app run infsh/video-audio-extractor --input '{"video_url": "https://video.mp4"}' > audio.json

# Transcribe the extracted audio
infsh app run infsh/fast-whisper-large-v3 --input '{"audio_url": "<audio-url>"}'
```

## Workflow: Video Subtitles

```bash
# 1. Transcribe video audio
infsh app run infsh/fast-whisper-large-v3 --input '{
  "audio_url": "https://video.mp4",
  "timestamps": true
}' > transcript.json

# 2. Use transcript for captions
infsh app run infsh/caption-videos --input '{
  "video_url": "https://video.mp4",
  "captions": "<transcript-from-step-1>"
}'
```

## Supported Languages

Whisper supports 99+ languages including:
English, Spanish, French, German, Italian, Portuguese, Chinese, Japanese, Korean, Arabic, Hindi, Russian, and many more.

## Use Cases

- **Meetings**: Transcribe recordings
- **Podcasts**: Generate transcripts
- **Subtitles**: Create captions for videos
- **Voice Notes**: Convert to searchable text
- **Interviews**: Transcription for research
- **Accessibility**: Make audio content accessible

## Output Format

Returns JSON with:
- `text`: Full transcription
- `segments`: Timestamped segments (if requested)
- `language`: Detected language

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# Text-to-speech (reverse direction)
npx skills add inference-sh/skills@text-to-speech

# Video generation (add captions)
npx skills add inference-sh/skills@ai-video-generation

# AI avatars (lipsync with transcripts)
npx skills add inference-sh/skills@ai-avatar-video
```

Browse all audio apps: `infsh app list --category audio`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Audio Transcription Example](https://inference.sh/docs/examples/audio-transcription) - Complete transcription guide
- [Apps Overview](https://inference.sh/docs/apps/overview) - Understanding the app ecosystem
