---
name: nano-banana
description: "Generate images with Google Gemini native image models via inference.sh CLI. Models: Gemini 3 Pro Image, Gemini 2.5 Flash Image. Capabilities: text-to-image, image editing, multi-image input. Triggers: nano banana, gemini image, gemini 3 pro image, gemini 2.5 flash image, google image generation, native image generation, gemini native image"
allowed-tools: Bash(infsh *)
---

# Nano Banana - Gemini Native Image Generation

Generate images with Google Gemini native image models via [inference.sh](https://inference.sh) CLI.

![Nano Banana](https://cloud.inference.sh/u/33sqbmzt3mrg2xxphnhw5g5ear/01k8d6xa9cwawrvzk9cgtsexfc.png)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

infsh app run google/gemini-3-pro-image-preview --input '{"prompt": "a banana in space, photorealistic"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Models

| Model | App ID | Speed | Quality |
|-------|--------|-------|---------|
| Gemini 3 Pro Image | `google/gemini-3-pro-image-preview` | Slower | Best |
| Gemini 2.5 Flash Image | `google/gemini-2-5-flash-image` | Fast | Excellent |

## Search Gemini Image Apps

```bash
infsh app list --search "gemini image"
```

## Examples

### Basic Text-to-Image

```bash
infsh app run google/gemini-3-pro-image-preview --input '{
  "prompt": "A futuristic cityscape at sunset with flying cars"
}'
```

### Multiple Images

```bash
infsh app run google/gemini-2-5-flash-image --input '{
  "prompt": "Minimalist logo design for a coffee shop",
  "num_images": 4
}'
```

### Custom Aspect Ratio

```bash
infsh app run google/gemini-3-pro-image-preview --input '{
  "prompt": "Panoramic mountain landscape with northern lights",
  "aspect_ratio": "16:9"
}'
```

### Image Editing (with input image)

```bash
infsh app run google/gemini-2-5-flash-image --input '{
  "prompt": "Add a rainbow in the sky",
  "images": ["https://example.com/landscape.jpg"]
}'
```

### High Resolution (4K)

```bash
infsh app run google/gemini-3-pro-image-preview --input '{
  "prompt": "Detailed illustration of a medieval castle",
  "resolution": "4K"
}'
```

### With Google Search Grounding

```bash
infsh app run google/gemini-3-pro-image-preview --input '{
  "prompt": "Current weather in Tokyo visualized as an artistic scene",
  "enable_google_search": true
}'
```

## Input Options

| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | string | **Required.** What to generate or change |
| `images` | array | Input images for editing (up to 14) |
| `num_images` | integer | Number of images to generate |
| `aspect_ratio` | string | Output ratio: "1:1", "16:9", "9:16", "4:3", "3:4", "auto" |
| `resolution` | string | "1K", "2K", "4K" (Gemini 3 Pro only) |
| `output_format` | string | Output format for images |
| `enable_google_search` | boolean | Enable real-time info grounding |

## Prompt Tips

**Styles**: photorealistic, illustration, watercolor, oil painting, digital art, anime, 3D render

**Composition**: close-up, wide shot, aerial view, macro, portrait, landscape

**Lighting**: natural light, studio lighting, golden hour, dramatic shadows, neon

**Details**: add specific details about textures, colors, mood, atmosphere

## Sample Workflow

```bash
# 1. Generate sample input to see all options
infsh app sample google/gemini-3-pro-image-preview --save input.json

# 2. Edit the prompt
# 3. Run
infsh app run google/gemini-3-pro-image-preview --input input.json
```

## Related Skills

```bash
# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# All image generation models
npx skills add inference-sh/skills@ai-image-generation

# Video generation (for image-to-video)
npx skills add inference-sh/skills@ai-video-generation
```

Browse all image apps: `infsh app list --category image`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Streaming Results](https://inference.sh/docs/api/sdk/streaming) - Real-time progress updates
- [File Handling](https://inference.sh/docs/api/sdk/files) - Working with images
