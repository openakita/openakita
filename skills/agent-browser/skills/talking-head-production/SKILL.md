---
name: talking-head-production
description: "Talking head video production with AI avatars, lipsync, and voiceover. Covers portrait requirements, audio quality, OmniHuman, PixVerse lipsync, Dia TTS. Use for: spokesperson videos, course content, social media, presentations, demos. Triggers: talking head, avatar video, lipsync, lip sync, ai spokesperson, virtual presenter, ai presenter, omnihuman, talking avatar, video presenter, ai talking head, presenter video, ai face video"
allowed-tools: Bash(infsh *)
---

# Talking Head Production

Create talking head videos with AI avatars and lipsync via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate dialogue audio
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] Welcome to our product tour. Today I will show you three features that will save you hours every week."
}'

# Create talking head video with OmniHuman
infsh app run bytedance/omnihuman-1-5 --input '{
  "image": "path/to/portrait.png",
  "audio": "path/to/dialogue.mp3"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Portrait Requirements

The source portrait image is critical. Poor portraits = poor video output.

### Must Have

| Requirement | Why | Spec |
|------------|-----|------|
| **Center-framed** | Avatar needs face in predictable position | Face centered in frame |
| **Head and shoulders** | Body visible for natural gestures | Crop below chest |
| **Eyes to camera** | Creates connection with viewer | Direct frontal gaze |
| **Neutral expression** | Starting point for animation | Slight smile OK, not laughing/frowning |
| **Clear face** | Model needs to detect features | No sunglasses, heavy shadows, or obstructions |
| **High resolution** | Detail preservation | Min 512x512 face region, ideally 1024x1024+ |

### Background

| Type | When to Use |
|------|-------------|
| Solid color | Professional, clean, easy to composite |
| Soft bokeh | Natural, lifestyle feel |
| Office/studio | Business context |
| Transparent (via bg removal) | Compositing into other scenes |

```bash
# Generate a professional portrait background
infsh app run falai/flux-dev-lora --input '{
  "prompt": "professional headshot photograph of a friendly business person, soft studio lighting, clean grey background, head and shoulders, direct eye contact, neutral pleasant expression, high quality portrait photography"
}'

# Or remove background from existing portrait
infsh app run <bg-removal-app> --input '{
  "image": "path/to/portrait-with-background.png"
}'
```

## Audio Quality

Audio quality directly impacts lipsync accuracy. Clean audio = accurate lip movement.

### Requirements

| Parameter | Target | Why |
|-----------|--------|-----|
| Background noise | None/minimal | Noise confuses lipsync timing |
| Volume | Consistent throughout | Prevents sync drift |
| Sample rate | 44.1kHz or 48kHz | Standard quality |
| Format | MP3 128kbps+ or WAV | Compatible with all tools |

### Generating Audio

```bash
# Simple narration
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] Hi there! I am excited to share something with you today. We have been working on a feature that our users have been requesting for months... and it is finally here."
}'

# With emotion and pacing
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] You know what is frustrating? Spending hours on tasks that should take minutes. (sighs) We have all been there. But what if I told you... there is a better way?"
}'
```

## Model Selection

| Model | App ID | Best For | Max Duration |
|-------|--------|----------|-------------|
| OmniHuman 1.5 | `bytedance/omnihuman-1-5` | Multi-character, gestures, high quality | ~30s per clip |
| OmniHuman 1.0 | `bytedance/omnihuman-1-0` | Single character, simpler | ~30s per clip |
| PixVerse Lipsync | `falai/pixverse-lipsync` | Quick lipsync on existing video | Short clips |
| Fabric | `falai/fabric-1-0` | Cloth/fabric animation on portraits | Short clips |

## Production Workflows

### Basic: Portrait + Audio -> Video

```bash
# 1. Generate or prepare audio
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] Your narration script here."
}'

# 2. Generate talking head
infsh app run bytedance/omnihuman-1-5 --input '{
  "image": "portrait.png",
  "audio": "narration.mp3"
}'
```

### With Captions

```bash
# 1-2. Same as above

# 3. Add captions to the talking head video
infsh app run infsh/caption-videos --input '{
  "video": "talking-head.mp4",
  "caption_file": "captions.srt"
}'
```

### Long-Form (Stitched Clips)

For content longer than 30 seconds, split into segments:

```bash
# Generate audio segments
infsh app run falai/dia-tts --input '{"prompt": "[S1] Segment one script."}' --no-wait
infsh app run falai/dia-tts --input '{"prompt": "[S1] Segment two script."}' --no-wait
infsh app run falai/dia-tts --input '{"prompt": "[S1] Segment three script."}' --no-wait

# Generate talking head for each segment (same portrait for consistency)
infsh app run bytedance/omnihuman-1-5 --input '{"image": "portrait.png", "audio": "segment1.mp3"}' --no-wait
infsh app run bytedance/omnihuman-1-5 --input '{"image": "portrait.png", "audio": "segment2.mp3"}' --no-wait
infsh app run bytedance/omnihuman-1-5 --input '{"image": "portrait.png", "audio": "segment3.mp3"}' --no-wait

# Merge all segments
infsh app run infsh/media-merger --input '{
  "media": ["segment1.mp4", "segment2.mp4", "segment3.mp4"]
}'
```

### Multi-Character Conversation

OmniHuman 1.5 supports up to 2 characters:

```bash
# 1. Generate dialogue with two speakers
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] So tell me about the new feature. [S2] Sure! We built a dashboard that shows real-time analytics. [S1] That sounds great. How long did it take? [S2] About two weeks from concept to launch."
}'

# 2. Create video with two characters
infsh app run bytedance/omnihuman-1-5 --input '{
  "image": "two-person-portrait.png",
  "audio": "dialogue.mp3"
}'
```

## Framing Guidelines

```
┌─────────────────────────────────┐
│          Headroom (minimal)     │
│  ┌───────────────────────────┐  │
│  │                           │  │
│  │     ● ─ ─ Eyes at 1/3 ─ ─│─ │ ← Eyes at top 1/3 line
│  │    /|\                    │  │
│  │     |   Head & shoulders  │  │
│  │    / \  visible           │  │
│  │                           │  │
│  └───────────────────────────┘  │
│       Crop below chest          │
└─────────────────────────────────┘
```

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Low-res portrait | Blurry face, poor lipsync | Use 1024x1024+ face region |
| Profile/side angle | Lipsync can't track mouth well | Use frontal or near-frontal |
| Noisy audio | Lipsync drifts, looks unnatural | Record clean or use TTS |
| Too-long clips | Quality degrades after 30s | Split into segments, stitch |
| Sunglasses/obstruction | Face features hidden | Clear face required |
| Inconsistent lighting | Uncanny when animated | Even, soft lighting |
| No captions | Loses silent/mobile viewers | Always add captions |

## Related Skills

```bash
npx skills add inference-sh/skills@ai-avatar-video
npx skills add inference-sh/skills@ai-video-generation
npx skills add inference-sh/skills@text-to-speech
```

Browse all apps: `infsh app list`
