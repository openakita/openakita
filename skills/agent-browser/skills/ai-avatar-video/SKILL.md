---
name: ai-avatar-video
description: "Create AI avatar and talking head videos with OmniHuman, Fabric, PixVerse via inference.sh CLI. Models: OmniHuman 1.5, OmniHuman 1.0, Fabric 1.0, PixVerse Lipsync. Capabilities: audio-driven avatars, lipsync videos, talking head generation, virtual presenters. Use for: AI presenters, explainer videos, virtual influencers, dubbing, marketing videos. Triggers: ai avatar, talking head, lipsync, avatar video, virtual presenter, ai spokesperson, audio driven video, heygen alternative, synthesia alternative, talking avatar, lip sync, video avatar, ai presenter, digital human"
allowed-tools: Bash(infsh *)
---

# AI Avatar & Talking Head Videos

Create AI avatars and talking head videos via [inference.sh](https://inference.sh) CLI.

![AI Avatar & Talking Head Videos](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg0tszs96s0n8z5gy8y5mbg7.jpeg)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Create avatar video from image + audio
infsh app run bytedance/omnihuman-1-5 --input '{
  "image_url": "https://portrait.jpg",
  "audio_url": "https://speech.mp3"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Available Models

| Model | App ID | Best For |
|-------|--------|----------|
| OmniHuman 1.5 | `bytedance/omnihuman-1-5` | Multi-character, best quality |
| OmniHuman 1.0 | `bytedance/omnihuman-1-0` | Single character |
| Fabric 1.0 | `falai/fabric-1-0` | Image talks with lipsync |
| PixVerse Lipsync | `falai/pixverse-lipsync` | Highly realistic |

## Search Avatar Apps

```bash
infsh app list --search "omnihuman"
infsh app list --search "lipsync"
infsh app list --search "fabric"
```

## Examples

### OmniHuman 1.5 (Multi-Character)

```bash
infsh app run bytedance/omnihuman-1-5 --input '{
  "image_url": "https://portrait.jpg",
  "audio_url": "https://speech.mp3"
}'
```

Supports specifying which character to drive in multi-person images.

### Fabric 1.0 (Image Talks)

```bash
infsh app run falai/fabric-1-0 --input '{
  "image_url": "https://face.jpg",
  "audio_url": "https://audio.mp3"
}'
```

### PixVerse Lipsync

```bash
infsh app run falai/pixverse-lipsync --input '{
  "image_url": "https://portrait.jpg",
  "audio_url": "https://speech.mp3"
}'
```

Generates highly realistic lipsync from any audio.

## Full Workflow: TTS + Avatar

```bash
# 1. Generate speech from text
infsh app run infsh/kokoro-tts --input '{
  "text": "Welcome to our product demo. Today I will show you..."
}' > speech.json

# 2. Create avatar video with the speech
infsh app run bytedance/omnihuman-1-5 --input '{
  "image_url": "https://presenter-photo.jpg",
  "audio_url": "<audio-url-from-step-1>"
}'
```

## Full Workflow: Dub Video in Another Language

```bash
# 1. Transcribe original video
infsh app run infsh/fast-whisper-large-v3 --input '{"audio_url": "https://video.mp4"}' > transcript.json

# 2. Translate text (manually or with an LLM)

# 3. Generate speech in new language
infsh app run infsh/kokoro-tts --input '{"text": "<translated-text>"}' > new_speech.json

# 4. Lipsync the original video with new audio
infsh app run infsh/latentsync-1-6 --input '{
  "video_url": "https://original-video.mp4",
  "audio_url": "<new-audio-url>"
}'
```

## Use Cases

- **Marketing**: Product demos with AI presenter
- **Education**: Course videos, explainers
- **Localization**: Dub content in multiple languages
- **Social Media**: Consistent virtual influencer
- **Corporate**: Training videos, announcements

## Tips

- Use high-quality portrait photos (front-facing, good lighting)
- Audio should be clear with minimal background noise
- OmniHuman 1.5 supports multiple people in one image
- LatentSync is best for syncing existing videos to new audio

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# Text-to-speech (generate audio for avatars)
npx skills add inference-sh/skills@text-to-speech

# Speech-to-text (transcribe for dubbing)
npx skills add inference-sh/skills@speech-to-text

# Video generation
npx skills add inference-sh/skills@ai-video-generation

# Image generation (create avatar images)
npx skills add inference-sh/skills@ai-image-generation
```

Browse all video apps: `infsh app list --category video`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Content Pipeline Example](https://inference.sh/docs/examples/content-pipeline) - Building media workflows
- [Streaming Results](https://inference.sh/docs/api/sdk/streaming) - Real-time progress updates
