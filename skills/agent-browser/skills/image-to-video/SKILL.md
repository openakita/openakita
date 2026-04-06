---
name: image-to-video
description: "Still-to-video conversion guide: model selection, motion prompting, and camera movement. Covers Wan 2.5 i2v, Seedance, Fabric, Grok Video with when to use each. Use for: animating images, creating video from stills, adding motion, product animations. Triggers: image to video, i2v, animate image, still to video, add motion to image, image animation, photo to video, animate still, wan i2v, image2video, bring image to life, animate photo, motion from image"
allowed-tools: Bash(infsh *)
---

# Image to Video

Convert still images to animated videos via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a still image
infsh app run falai/flux-dev-lora --input '{
  "prompt": "serene mountain lake at sunset, snow-capped peaks reflected in still water, golden hour light, landscape photography",
  "width": 1248,
  "height": 832
}'

# Animate it
infsh app run falai/wan-2-5-i2v --input '{
  "prompt": "gentle ripples on the lake surface, clouds slowly drifting, warm light shifting, birds flying in the distance",
  "image": "path/to/lake-image.png"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Model Selection

| Model | App ID | Best For | Motion Style |
|-------|--------|----------|-------------|
| **Wan 2.5 i2v** | `falai/wan-2-5-i2v` | Realistic motion, natural movement | Photorealistic, subtle |
| **Seedance 1.5 Pro** | `bytedance/seedance-1-5-pro` | Stylized, creative, animation-like | Artistic, expressive |
| **Seedance 1.0 Pro** | `bytedance/seedance-1-0-pro` | General purpose, good quality | Balanced |
| **Fabric 1.0** | `falai/fabric-1-0` | Cloth, fabric, liquid, flowing materials | Physics-based flow |
| **Grok Imagine Video** | `xai/grok-imagine-video` | General animation, text-guided | Versatile |

### When to Use Each

| Scenario | Best Model | Why |
|----------|-----------|-----|
| Landscape with water/clouds | **Wan 2.5 i2v** | Best at natural, realistic motion |
| Portrait with subtle expression | **Wan 2.5 i2v** | Maintains face fidelity |
| Product with fabric/cloth | **Fabric 1.0** | Specialized in material physics |
| Flag waving, curtain flowing | **Fabric 1.0** | Cloth simulation |
| Illustrated/artistic image | **Seedance** | Matches stylized content |
| General "bring to life" | **Seedance 1.5 Pro** | Good all-rounder |
| Quick test/iteration | **Seedance 1.0 Lite** | Fastest, 720p |

## Motion Types

### Camera Movement

| Movement | Prompt Keyword | Effect |
|----------|---------------|--------|
| Push in / Dolly forward | "slow dolly forward", "camera pushes in" | Increasing intimacy/focus |
| Pull out / Dolly back | "camera pulls back", "slow zoom out" | Reveal, context |
| Pan left/right | "camera pans slowly to the right" | Scanning, following |
| Tilt up/down | "camera tilts upward" | Revealing height |
| Orbit | "camera orbits around the subject" | 3D exploration |
| Crane up | "camera rises upward" | Grand reveal |
| Static | (no camera movement prompt) | Subject motion only |

### Subject Motion

| Type | Prompt Examples |
|------|----------------|
| Natural elements | "water rippling", "clouds drifting", "leaves rustling in breeze" |
| Hair/clothing | "hair blowing gently in wind", "dress fabric flowing" |
| Atmospheric | "fog slowly rolling", "dust particles floating in light beams" |
| Character | "person slowly turns to camera", "subtle breathing motion" |
| Mechanical | "gears turning", "clock hands moving" |
| Liquid | "coffee steam rising", "paint dripping", "water pouring" |

## Prompting Best Practices

### The Golden Rule: Subtle > Dramatic

AI video models produce better results with **gentle, subtle motion** than dramatic action. Requesting too much movement causes distortion and artifacts.

```
❌ "person running and jumping over obstacles while the camera spins"
✅ "person slowly walking forward, gentle breeze, camera follows alongside"

❌ "explosion with debris flying everywhere"
✅ "candle flame flickering gently, warm ambient light shifting"

❌ "fast zoom into the eyes with dramatic camera shake"
✅ "slow dolly forward toward the subject, subtle focus shift"
```

### Prompt Structure

```
[Camera movement] + [Subject motion] + [Atmospheric effects] + [Mood/pace]
```

### Examples by Scenario

```bash
# Landscape animation
infsh app run falai/wan-2-5-i2v --input '{
  "prompt": "gentle camera pan right, water reflecting moving clouds, trees swaying slightly in breeze, warm golden light, peaceful and slow",
  "image": "landscape.png"
}'

# Portrait animation
infsh app run falai/wan-2-5-i2v --input '{
  "prompt": "subtle breathing motion, slight head turn, natural eye blink, hair moving gently, soft ambient lighting shifts",
  "image": "portrait.png"
}'

# Product shot animation
infsh app run bytedance/seedance-1-5-pro --input '{
  "prompt": "slow 360 degree orbit around the product, gentle spotlight movement, subtle reflections shifting, premium product showcase, smooth motion",
  "image": "product.png"
}'

# Fabric/cloth animation
infsh app run falai/fabric-1-0 --input '{
  "prompt": "fabric flowing and rippling in gentle wind, natural cloth physics, soft movement",
  "image": "fabric-scene.png"
}'

# Architectural visualization
infsh app run falai/wan-2-5-i2v --input '{
  "prompt": "slow dolly forward through the entrance, slight camera tilt upward, ambient light filtering through windows, dust particles in light beams",
  "image": "building-interior.png"
}'
```

## Duration Guidelines

| Duration | Quality | Use For |
|----------|---------|---------|
| 2-3 seconds | Highest quality | GIFs, looping backgrounds, cinemagraphs |
| 4-5 seconds | High quality | Social media posts, product reveals |
| 6-8 seconds | Good quality | Short clips, transitions |
| 10+ seconds | Quality degrades | Avoid unless stitching shorter clips |

### Extending Duration

For longer videos, generate multiple short clips and stitch:

```bash
# Generate 3 clips from the same image with progressive motion
infsh app run falai/wan-2-5-i2v --input '{
  "prompt": "slow pan left, gentle water motion",
  "image": "scene.png"
}' --no-wait

infsh app run falai/wan-2-5-i2v --input '{
  "prompt": "continuing pan, clouds shifting, light changing",
  "image": "scene.png"
}' --no-wait

# Stitch together
infsh app run infsh/media-merger --input '{
  "media": ["clip1.mp4", "clip2.mp4"]
}'
```

## The Full Workflow

### Still-to-Final-Video Pipeline

```bash
# 1. Generate source image (best quality)
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "cinematic landscape, misty mountains at dawn, lake in foreground, dramatic clouds, golden hour, 4K quality, professional photography",
  "size": "2K"
}'

# 2. Animate the image
infsh app run falai/wan-2-5-i2v --input '{
  "prompt": "gentle mist rolling through the valley, lake surface rippling, clouds slowly moving, birds in distance, warm light shifting",
  "image": "landscape.png"
}'

# 3. Upscale video if needed
infsh app run falai/topaz-video-upscaler --input '{
  "video": "animated-landscape.mp4"
}'

# 4. Add ambient audio
infsh app run infsh/hunyuanvideo-foley --input '{
  "video": "animated-landscape.mp4",
  "prompt": "gentle nature ambience, distant birds, soft wind, water lapping"
}'

# 5. Merge video with audio
infsh app run infsh/video-audio-merger --input '{
  "video": "upscaled-landscape.mp4",
  "audio": "ambient-audio.mp3"
}'
```

## Cinemagraph Effect

A cinemagraph is a still photo where only one element moves (e.g., waterfall moving in an otherwise frozen scene). To achieve this:

1. Generate the still image with the motion element clearly defined
2. Prompt for motion only in that specific element
3. Keep to 2-4 seconds for seamless looping

```bash
infsh app run falai/wan-2-5-i2v --input '{
  "prompt": "only the waterfall is moving, everything else remains perfectly still, water cascading smoothly, rest of scene frozen",
  "image": "waterfall-scene.png"
}'
```

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Too much motion requested | Distortion, artifacts, warping | Subtle > dramatic, always |
| Wrong model for content type | Poor results | Use selection guide above |
| Clips too long (10s+) | Quality degrades significantly | Keep to 3-5 seconds, stitch if needed |
| No camera movement specified | Random/unpredictable motion | Always specify camera behavior |
| Conflicting motion directions | Chaotic, unnatural | One primary motion direction |
| Low-res source image | Low-res video output | Start with highest quality source |
| Complex action scenes | Models can't handle | Keep motion simple and natural |

## Related Skills

```bash
npx skills add inference-sh/skills@ai-video-generation
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@video-prompting-guide
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
