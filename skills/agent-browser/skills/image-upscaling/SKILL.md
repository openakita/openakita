---
name: image-upscaling
description: "Upscale and enhance images with Real-ESRGAN, Thera, Topaz, FLUX Upscaler via inference.sh CLI. Models: Real-ESRGAN, Thera (any size), FLUX Dev Upscaler, Topaz Image Upscaler. Use for: enhance low-res images, upscale AI art, restore old photos, increase resolution. Triggers: upscale image, image upscaler, enhance image, increase resolution, real esrgan, ai upscale, super resolution, image enhancement, upscaling, enlarge image, higher resolution, 4k upscale, hd upscale"
allowed-tools: Bash(infsh *)
---

# Image Upscaling

Upscale and enhance images via [inference.sh](https://inference.sh) CLI.

![Image Upscaling](https://cloud.inference.sh/u/33sqbmzt3mrg2xxphnhw5g5ear/01k8d77p126y82zfecnt46hy4h.png)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

infsh app run infsh/real-esrgan --input '{"image_url": "https://your-image.jpg"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Available Upscalers

| Model | App ID | Best For |
|-------|--------|----------|
| Topaz Image Upscaler | `falai/topaz-image-upscaler` | Professional quality, any image |

## Examples

### Upscale Any Image

```bash
infsh app run falai/topaz-image-upscaler --input '{"image_url": "https://low-res-image.jpg"}'
```

### Workflow: Generate and Upscale

```bash
# 1. Generate image with FLUX Klein (fast)
infsh app run falai/flux-2-klein-lora --input '{"prompt": "landscape painting"}' > image.json

# 2. Upscale the result
infsh app run falai/topaz-image-upscaler --input '{"image_url": "<url-from-step-1>"}'
```

## Use Cases

- **AI Art**: Upscale generated images for print
- **Old Photos**: Restore and enhance resolution
- **Web Images**: Prepare for high-DPI displays
- **Print**: Increase resolution for large prints
- **Thumbnails**: Create high-res versions

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# Image generation (generate then upscale)
npx skills add inference-sh/skills@ai-image-generation

# FLUX models
npx skills add inference-sh/skills@flux-image

# Background removal
npx skills add inference-sh/skills@background-removal
```

Browse all image apps: `infsh app list --category image`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Image Generation Example](https://inference.sh/docs/examples/image-generation) - Complete image workflow guide
- [Apps Overview](https://inference.sh/docs/apps/overview) - Understanding the app ecosystem
