---
name: nano-banana-2
description: "Generate images with Google Gemini 3.1 Flash Image Preview (Nano Banana 2) via inference.sh CLI. Capabilities: text-to-image, image editing, multi-image input (up to 14 images), Google Search grounding. Triggers: nano banana 2, nanobanana 2, gemini 3.1 flash image, gemini 3 1 flash image preview, google image generation"
allowed-tools: Bash(infsh *)
---

# Nano Banana 2 - Gemini 3.1 Flash Image Preview

Generate images with Google Gemini 3.1 Flash Image Preview via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

infsh app run google/gemini-3-1-flash-image-preview --input '{"prompt": "a banana in space, photorealistic"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Examples

### Basic Text-to-Image

```bash
infsh app run google/gemini-3-1-flash-image-preview --input '{
  "prompt": "A futuristic cityscape at sunset with flying cars"
}'
```

### Multiple Images

```bash
infsh app run google/gemini-3-1-flash-image-preview --input '{
  "prompt": "Minimalist logo design for a coffee shop",
  "num_images": 4
}'
```

### Custom Aspect Ratio

```bash
infsh app run google/gemini-3-1-flash-image-preview --input '{
  "prompt": "Panoramic mountain landscape with northern lights",
  "aspect_ratio": "16:9"
}'
```

### Image Editing (with input images)

```bash
infsh app run google/gemini-3-1-flash-image-preview --input '{
  "prompt": "Add a rainbow in the sky",
  "images": ["https://example.com/landscape.jpg"]
}'
```

### High Resolution (4K)

```bash
infsh app run google/gemini-3-1-flash-image-preview --input '{
  "prompt": "Detailed illustration of a medieval castle",
  "resolution": "4K"
}'
```

### With Google Search Grounding

```bash
infsh app run google/gemini-3-1-flash-image-preview --input '{
  "prompt": "Current weather in Tokyo visualized as an artistic scene",
  "enable_google_search": true
}'
```

## Input Options

| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | string | **Required.** What to generate or change |
| `images` | array | Input images for editing (up to 14). Supported: JPEG, PNG, WebP |
| `num_images` | integer | Number of images to generate |
| `aspect_ratio` | string | Output ratio: "1:1", "16:9", "9:16", "4:3", "3:4", "auto" |
| `resolution` | string | "1K", "2K", "4K" (default: 1K) |
| `output_format` | string | Output format for images |
| `enable_google_search` | boolean | Enable real-time info grounding (weather, news, etc.) |

## Output

| Field | Type | Description |
|-------|------|-------------|
| `images` | array | The generated or edited images |
| `description` | string | Text description or response from the model |
| `output_meta` | object | Metadata about inputs/outputs for pricing |

## Prompt Tips

**Styles**: photorealistic, illustration, watercolor, oil painting, digital art, anime, 3D render

**Composition**: close-up, wide shot, aerial view, macro, portrait, landscape

**Lighting**: natural light, studio lighting, golden hour, dramatic shadows, neon

**Details**: add specific details about textures, colors, mood, atmosphere

## Sample Workflow

```bash
# 1. Generate sample input to see all options
infsh app sample google/gemini-3-1-flash-image-preview --save input.json

# 2. Edit the prompt
# 3. Run
infsh app run google/gemini-3-1-flash-image-preview --input input.json
```

## Python SDK

```python
from inferencesh import inference

client = inference()

# Basic generation
result = client.run({
    "app": "google/gemini-3-1-flash-image-preview@0c7ma1ex",
    "input": {
        "prompt": "A banana in space, photorealistic"
    }
})
print(result["output"])

# Stream live updates
for update in client.run({
    "app": "google/gemini-3-1-flash-image-preview@0c7ma1ex",
    "input": {
        "prompt": "A futuristic cityscape at sunset"
    }
}, stream=True):
    if update.get("progress"):
        print(f"progress: {update['progress']}%")
    if update.get("output"):
        print(f"output: {update['output']}")
```

## Related Skills

```bash
# Original Nano Banana (Gemini 3 Pro Image, Gemini 2.5 Flash Image)
npx skills add inference-sh/skills@nano-banana

# Full platform skill (all 150+ apps)
npx skills add inference-sh/skills@inference-sh

# All image generation models
npx skills add inference-sh/skills@ai-image-generation
```

Browse all image apps: `infsh app list --category image`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Streaming Results](https://inference.sh/docs/api/sdk/streaming) - Real-time progress updates
- [File Handling](https://inference.sh/docs/api/sdk/files) - Working with images
