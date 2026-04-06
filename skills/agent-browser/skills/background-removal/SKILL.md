---
name: background-removal
description: "Remove backgrounds from images with BiRefNet via inference.sh CLI. Model: BiRefNet (high accuracy background removal). Use for: product photos, portraits, e-commerce, transparent PNGs, photo editing. Triggers: remove background, background removal, remove bg, transparent background, cut out image, background remover, rembg, product photo editing, cutout, transparent png, bg removal, photo cutout"
allowed-tools: Bash(infsh *)
---

# Background Removal

Remove backgrounds from images via [inference.sh](https://inference.sh) CLI.

![Background Removal](https://cloud.inference.sh/u/33sqbmzt3mrg2xxphnhw5g5ear/01k8d7y07rpmnv85hz2xvhjvbb.png)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

infsh app run infsh/birefnet --input '{"image_url": "https://your-photo.jpg"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## How To

Use Reve for image editing including background changes:

```bash
infsh app run falai/reve --input '{
  "prompt": "remove the background, make it transparent",
  "image_url": "https://portrait.jpg"
}'
```

Or change background directly:

```bash
infsh app run falai/reve --input '{
  "prompt": "change the background to a beach",
  "image_url": "https://product-photo.jpg"
}'
```

## Workflow: Generate and Edit

```bash
# 1. Generate an image
infsh app run falai/flux-dev-lora --input '{"prompt": "a cute robot mascot"}' > robot.json

# 2. Edit with Reve
infsh app run falai/reve --input '{
  "prompt": "remove background, transparent",
  "image_url": "<url-from-step-1>"
}'
```

## Use Cases

- **E-commerce**: Clean product photos
- **Portraits**: Professional headshots
- **Marketing**: Assets for design
- **Social Media**: Profile pictures
- **Design**: Elements for compositions

## Output

Returns a PNG with transparent background.

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# Image generation
npx skills add inference-sh/skills@ai-image-generation

# FLUX models (including inpainting)
npx skills add inference-sh/skills@flux-image

# Upscaling
npx skills add inference-sh/skills@image-upscaling
```

Browse all image apps: `infsh app list --category image`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Image Generation Example](https://inference.sh/docs/examples/image-generation) - Complete image workflow guide
- [Apps Overview](https://inference.sh/docs/apps/overview) - Understanding the app ecosystem
