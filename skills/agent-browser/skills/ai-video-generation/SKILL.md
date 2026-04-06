---
name: ai-video-generation
description: "Generate AI videos with Google Veo, Seedance, Wan, Grok and 40+ models via inference.sh CLI. Models: Veo 3.1, Veo 3, Seedance 1.5 Pro, Wan 2.5, Grok Imagine Video, OmniHuman, Fabric, HunyuanVideo. Capabilities: text-to-video, image-to-video, lipsync, avatar animation, video upscaling, foley sound. Use for: social media videos, marketing content, explainer videos, product demos, AI avatars. Triggers: video generation, ai video, text to video, image to video, veo, animate image, video from image, ai animation, video generator, generate video, t2v, i2v, ai video maker, create video with ai, runway alternative, pika alternative, sora alternative, kling alternative"
allowed-tools: Bash(infsh *)
---

# AI Video Generation

Generate videos with 40+ AI models via [inference.sh](https://inference.sh) CLI.

![AI Video Generation](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg2c0egyg243mnyth4y6g51q.jpeg)

## Quick Start

```bash
# Install CLI
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a video with Veo
infsh app run google/veo-3-1-fast --input '{"prompt": "drone shot flying over a forest"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Available Models

### Text-to-Video

| Model | App ID | Best For |
|-------|--------|----------|
| Veo 3.1 Fast | `google/veo-3-1-fast` | Fast, with optional audio |
| Veo 3.1 | `google/veo-3-1` | Best quality, frame interpolation |
| Veo 3 | `google/veo-3` | High quality with audio |
| Veo 3 Fast | `google/veo-3-fast` | Fast with audio |
| Veo 2 | `google/veo-2` | Realistic videos |
| Grok Video | `xai/grok-imagine-video` | xAI, configurable duration |
| Seedance 1.5 Pro | `bytedance/seedance-1-5-pro` | With first-frame control |
| Seedance 1.0 Pro | `bytedance/seedance-1-0-pro` | Up to 1080p |

### Image-to-Video

| Model | App ID | Best For |
|-------|--------|----------|
| Wan 2.5 | `falai/wan-2-5` | Animate any image |
| Wan 2.5 I2V | `falai/wan-2-5-i2v` | High quality i2v |
| Seedance Lite | `bytedance/seedance-1-0-lite` | Lightweight 720p |

### Avatar / Lipsync

| Model | App ID | Best For |
|-------|--------|----------|
| OmniHuman 1.5 | `bytedance/omnihuman-1-5` | Multi-character |
| OmniHuman 1.0 | `bytedance/omnihuman-1-0` | Single character |
| Fabric 1.0 | `falai/fabric-1-0` | Image talks with lipsync |
| PixVerse Lipsync | `falai/pixverse-lipsync` | Realistic lipsync |

### Utilities

| Tool | App ID | Description |
|------|--------|-------------|
| HunyuanVideo Foley | `infsh/hunyuanvideo-foley` | Add sound effects to video |
| Topaz Upscaler | `falai/topaz-video-upscaler` | Upscale video quality |
| Media Merger | `infsh/media-merger` | Merge videos with transitions |

## Browse All Video Apps

```bash
infsh app list --category video
```

## Examples

### Text-to-Video with Veo

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "A timelapse of a flower blooming in a garden"
}'
```

### Grok Video

```bash
infsh app run xai/grok-imagine-video --input '{
  "prompt": "Waves crashing on a beach at sunset",
  "duration": 5
}'
```

### Image-to-Video with Wan 2.5

```bash
infsh app run falai/wan-2-5 --input '{
  "image_url": "https://your-image.jpg"
}'
```

### AI Avatar / Talking Head

```bash
infsh app run bytedance/omnihuman-1-5 --input '{
  "image_url": "https://portrait.jpg",
  "audio_url": "https://speech.mp3"
}'
```

### Fabric Lipsync

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

### Video Upscaling

```bash
infsh app run falai/topaz-video-upscaler --input '{"video_url": "https://..."}'
```

### Add Sound Effects (Foley)

```bash
infsh app run infsh/hunyuanvideo-foley --input '{
  "video_url": "https://silent-video.mp4",
  "prompt": "footsteps on gravel, birds chirping"
}'
```

### Merge Videos

```bash
infsh app run infsh/media-merger --input '{
  "videos": ["https://clip1.mp4", "https://clip2.mp4"],
  "transition": "fade"
}'
```

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# Google Veo specific
npx skills add inference-sh/skills@google-veo

# AI avatars & lipsync
npx skills add inference-sh/skills@ai-avatar-video

# Text-to-speech (for video narration)
npx skills add inference-sh/skills@text-to-speech

# Image generation (for image-to-video)
npx skills add inference-sh/skills@ai-image-generation

# Twitter (post videos)
npx skills add inference-sh/skills@twitter-automation
```

Browse all apps: `infsh app list`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Streaming Results](https://inference.sh/docs/api/sdk/streaming) - Real-time progress updates
- [Content Pipeline Example](https://inference.sh/docs/examples/content-pipeline) - Building media workflows
