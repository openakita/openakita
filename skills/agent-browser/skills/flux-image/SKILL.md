---
name: flux-image
description: "Generate images with FLUX models (Black Forest Labs) via inference.sh CLI. Models: FLUX Dev LoRA, FLUX.2 Klein LoRA with custom style adaptation. Capabilities: text-to-image, image-to-image, LoRA fine-tuning, custom styles. Triggers: flux, flux.2, flux dev, flux schnell, flux pro, black forest labs, flux image, flux ai, flux model, flux lora"
allowed-tools: Bash(infsh *)
---

# FLUX Image Generation

Generate images with FLUX models via [inference.sh](https://inference.sh) CLI.

![FLUX Image Generation](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg0v0nz7wv0qwqjtq1cam52z.jpeg)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

infsh app run falai/flux-dev-lora --input '{"prompt": "a futuristic city at night"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## FLUX Models

| Model | App ID | Speed | Quality | Use Case |
|-------|--------|-------|---------|----------|
| FLUX Dev LoRA | `falai/flux-dev-lora` | Medium | Highest | Production, custom styles |
| FLUX.2 Klein LoRA | `falai/flux-2-klein-lora` | Fastest | Good | Fast iteration, 4B/9B sizes |

## Examples

### High-Quality Generation

```bash
infsh app run falai/flux-dev-lora --input '{
  "prompt": "professional product photo of headphones, studio lighting, white background"
}'
```

### Fast Generation (Klein)

```bash
infsh app run falai/flux-2-klein-lora --input '{"prompt": "abstract art, colorful"}'
```

### With LoRA Custom Styles

```bash
infsh app sample falai/flux-dev-lora --save input.json

# Edit to add lora_url for custom style
infsh app run falai/flux-dev-lora --input input.json
```

### Image-to-Image

```bash
infsh app run falai/flux-dev-lora --input '{
  "prompt": "transform to watercolor style",
  "image_url": "https://your-image.jpg"
}'
```

## For Other Image Tasks

```bash
# Image editing with natural language
infsh app run falai/reve --input '{"prompt": "change background to beach"}'

# Upscaling
infsh app run falai/topaz-image-upscaler --input '{"image_url": "https://..."}'
```

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# All image generation models
npx skills add inference-sh/skills@ai-image-generation

# Upscaling
npx skills add inference-sh/skills@image-upscaling
```

Browse all apps: `infsh app list`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Image Generation Example](https://inference.sh/docs/examples/image-generation) - Complete image generation guide
- [Streaming Results](https://inference.sh/docs/api/sdk/streaming) - Real-time progress updates
