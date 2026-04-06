---
name: google-veo
description: "Generate videos with Google Veo models via inference.sh CLI. Models: Veo 3.1, Veo 3.1 Fast, Veo 3, Veo 3 Fast, Veo 2. Capabilities: text-to-video, cinematic output, high quality video generation. Triggers: veo, google veo, veo 3, veo 2, veo 3.1, vertex ai video, google video generation, google video ai, veo model, veo video"
allowed-tools: Bash(infsh *)
---

# Google Veo Video Generation

Generate videos with Google Veo models via [inference.sh](https://inference.sh) CLI.

![Google Veo Video Generation](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg2c0egyg243mnyth4y6g51q.jpeg)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

infsh app run google/veo-3-1-fast --input '{"prompt": "drone shot over a mountain lake"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Veo Models

| Model | App ID | Speed | Quality |
|-------|--------|-------|---------|
| Veo 3.1 | `google/veo-3-1` | Slower | Best |
| Veo 3.1 Fast | `google/veo-3-1-fast` | Fast | Excellent |
| Veo 3 | `google/veo-3` | Medium | Excellent |
| Veo 3 Fast | `google/veo-3-fast` | Fast | Very Good |
| Veo 2 | `google/veo-2` | Medium | Good |

## Search Veo Apps

```bash
infsh app list --search "veo"
```

## Examples

### Cinematic Shot

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Cinematic drone shot flying through a misty forest at sunrise, volumetric lighting"
}'
```

### Product Demo

```bash
infsh app run google/veo-3 --input '{
  "prompt": "Sleek smartphone rotating on a dark reflective surface, studio lighting"
}'
```

### Nature Scene

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Timelapse of clouds moving over a mountain range, golden hour"
}'
```

### Action Shot

```bash
infsh app run google/veo-3 --input '{
  "prompt": "Slow motion water droplet splashing into a pool, macro shot"
}'
```

### Urban Scene

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Busy city street at night with neon signs and rain reflections, Tokyo style"
}'
```

## Prompt Tips

**Camera movements**: drone shot, tracking shot, pan, zoom, dolly, steadicam

**Lighting**: golden hour, blue hour, studio lighting, volumetric, neon, natural

**Style**: cinematic, documentary, commercial, artistic, realistic

**Timing**: slow motion, timelapse, real-time

## Sample Workflow

```bash
# 1. Generate sample input to see all options
infsh app sample google/veo-3-1-fast --save input.json

# 2. Edit the prompt
# 3. Run
infsh app run google/veo-3-1-fast --input input.json
```

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# All video generation models
npx skills add inference-sh/skills@ai-video-generation

# AI avatars & lipsync
npx skills add inference-sh/skills@ai-avatar-video

# Image generation (for image-to-video)
npx skills add inference-sh/skills@ai-image-generation
```

Browse all video apps: `infsh app list --category video`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Streaming Results](https://inference.sh/docs/api/sdk/streaming) - Real-time progress updates
- [Content Pipeline Example](https://inference.sh/docs/examples/content-pipeline) - Building media workflows
