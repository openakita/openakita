---
name: text-to-speech
description: "Convert text to natural speech with DIA TTS, Kokoro, Chatterbox, and more via inference.sh CLI. Models: DIA TTS (conversational), Kokoro TTS, Chatterbox, Higgs Audio, VibeVoice (podcasts). Capabilities: text-to-speech, voice cloning, multi-speaker dialogue, podcast generation, expressive speech. Use for: voiceovers, audiobooks, podcasts, accessibility, video narration, IVR, voice assistants. Triggers: text to speech, tts, voice generation, ai voice, speech synthesis, voice over, generate speech, ai narrator, voice cloning, text to audio, elevenlabs alternative, voice ai, ai voiceover, speech generator, natural voice"
allowed-tools: Bash(infsh *)
---

# Text-to-Speech

Convert text to natural speech via [inference.sh](https://inference.sh) CLI.

![Text-to-Speech](https://cloud.inference.sh/u/4mg21r6ta37mpaz6ktzwtt8krr/01jz00krptarq4bwm89g539aea.png)

## Quick Start

```bash
# Install CLI
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate speech
infsh app run infsh/kokoro-tts --input '{"text": "Hello, welcome to our product demo."}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Available Models

| Model | App ID | Best For |
|-------|--------|----------|
| DIA TTS | `infsh/dia-tts` | Conversational, expressive |
| Kokoro TTS | `infsh/kokoro-tts` | Fast, natural |
| Chatterbox | `infsh/chatterbox` | General purpose |
| Higgs Audio | `infsh/higgs-audio` | Emotional control |
| VibeVoice | `infsh/vibevoice` | Podcasts, long-form |

## Browse All Audio Apps

```bash
infsh app list --category audio
```

## Examples

### Basic Text-to-Speech

```bash
infsh app run infsh/kokoro-tts --input '{"text": "Welcome to our tutorial."}'
```

### Conversational TTS with DIA

```bash
infsh app sample infsh/dia-tts --save input.json

# Edit input.json:
# {
#   "text": "Hey! How are you doing today? I'm really excited to share this with you.",
#   "voice": "conversational"
# }

infsh app run infsh/dia-tts --input input.json
```

### Long-form Audio (Podcasts)

```bash
infsh app sample infsh/vibevoice --save input.json

# Edit input.json with your podcast script
infsh app run infsh/vibevoice --input input.json
```

### Expressive Speech with Higgs

```bash
infsh app sample infsh/higgs-audio --save input.json

# {
#   "text": "This is absolutely incredible!",
#   "emotion": "excited"
# }

infsh app run infsh/higgs-audio --input input.json
```

## Use Cases

- **Voiceovers**: Product demos, explainer videos
- **Audiobooks**: Convert text to spoken word
- **Podcasts**: Generate podcast episodes
- **Accessibility**: Make content accessible
- **IVR**: Phone system voice prompts
- **Video Narration**: Add narration to videos

## Combine with Video

Generate speech, then create a talking head video:

```bash
# 1. Generate speech
infsh app run infsh/kokoro-tts --input '{"text": "Your script here"}' > speech.json

# 2. Use the audio URL with OmniHuman for avatar video
infsh app run bytedance/omnihuman-1-5 --input '{
  "image_url": "https://portrait.jpg",
  "audio_url": "<audio-url-from-step-1>"
}'
```

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# AI avatars (combine TTS with talking heads)
npx skills add inference-sh/skills@ai-avatar-video

# AI music generation
npx skills add inference-sh/skills@ai-music-generation

# Speech-to-text (transcription)
npx skills add inference-sh/skills@speech-to-text

# Video generation
npx skills add inference-sh/skills@ai-video-generation
```

Browse all apps: `infsh app list`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Audio Transcription Example](https://inference.sh/docs/examples/audio-transcription) - Audio processing workflows
- [Apps Overview](https://inference.sh/docs/apps/overview) - Understanding the app ecosystem
