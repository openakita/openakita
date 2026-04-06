---
name: video-prompting-guide
description: "Best practices and techniques for writing effective AI video generation prompts. Covers: Veo, Seedance, Wan, Grok, Kling, Runway, Pika, Sora prompting strategies. Learn: shot types, camera movements, lighting, pacing, style keywords, negative prompts. Use for: improving video quality, getting consistent results, professional video prompts. Triggers: video prompt, how to prompt video, veo prompts, video generation tips, better ai video, video prompt engineering, video prompt guide, video prompt template, ai video tips, video prompt best practices, video prompt examples, cinematography prompts"
allowed-tools: Bash(infsh *)
---

# Video Prompting Guide

Best practices for writing effective AI video generation prompts via [inference.sh](https://inference.sh).

![Video Prompting Guide](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg2c0egyg243mnyth4y6g51q.jpeg)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Well-structured video prompt
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Cinematic tracking shot of a red sports car driving through Tokyo at night, neon lights reflecting on wet streets, rain falling, 4K, shallow depth of field"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Prompt Structure Formula

```
[Shot Type] + [Subject] + [Action] + [Setting] + [Lighting] + [Style] + [Technical]
```

### Example Breakdown

```
"Slow motion close-up of coffee being poured into a white ceramic cup,
steam rising, morning sunlight streaming through window, warm color grading,
cinematic, 4K, shallow depth of field"
```

- **Shot Type**: Slow motion close-up
- **Subject**: Coffee
- **Action**: Being poured
- **Setting**: White ceramic cup, window
- **Lighting**: Morning sunlight
- **Style**: Warm color grading, cinematic
- **Technical**: 4K, shallow depth of field

## Shot Types

| Shot Type | Description | Use For |
|-----------|-------------|---------|
| Wide shot | Shows entire scene | Establishing location |
| Medium shot | Waist-up framing | Conversations, actions |
| Close-up | Face or detail | Emotion, product detail |
| Extreme close-up | Single feature | Drama, texture |
| Aerial shot | Bird's eye view | Landscapes, scale |
| Low angle | Camera looking up | Power, grandeur |
| High angle | Camera looking down | Vulnerability |
| Dutch angle | Tilted camera | Unease, tension |
| POV shot | First person view | Immersion |

## Camera Movements

| Movement | Description | Effect |
|----------|-------------|--------|
| Tracking shot | Camera follows subject | Dynamic, engaging |
| Dolly in/out | Camera moves toward/away | Focus, reveal |
| Pan | Horizontal rotation | Survey scene |
| Tilt | Vertical rotation | Reveal height |
| Crane shot | Vertical + horizontal | Dramatic reveal |
| Handheld | Slight shake | Realism, urgency |
| Steadicam | Smooth following | Professional, cinematic |
| Zoom | Lens zoom in/out | Quick focus change |
| Static | No movement | Contemplation, stability |

## Lighting Keywords

| Keyword | Effect |
|---------|--------|
| Golden hour | Warm, soft, romantic |
| Blue hour | Cool, moody, twilight |
| High key | Bright, minimal shadows |
| Low key | Dark, dramatic shadows |
| Rim lighting | Subject outlined with light |
| Backlit | Light from behind subject |
| Soft lighting | Gentle, flattering |
| Hard lighting | Sharp shadows, contrast |
| Neon | Colorful, urban, cyberpunk |
| Natural lighting | Realistic, documentary |

## Style Keywords

### Cinematic Styles

```
cinematic, film grain, anamorphic lens, letterbox,
shallow depth of field, bokeh, 35mm film,
color grading, theatrical
```

### Visual Aesthetics

```
minimalist, maximalist, vintage, retro, futuristic,
cyberpunk, steampunk, noir, pastel, vibrant,
muted colors, high contrast, desaturated
```

### Quality Keywords

```
4K, 8K, high resolution, photorealistic,
hyperrealistic, ultra detailed, professional,
broadcast quality, HDR
```

## Prompt Examples by Use Case

### Product Demo

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Smooth tracking shot around a sleek smartphone on a white pedestal, soft studio lighting, product photography style, reflections on surface, 4K, shallow depth of field"
}'
```

### Nature Documentary

```bash
infsh app run google/veo-3-1 --input '{
  "prompt": "Slow motion extreme close-up of a hummingbird hovering at a red flower, wings in motion blur, shallow depth of field, golden hour lighting, National Geographic style"
}'
```

### Urban Lifestyle

```bash
infsh app run google/veo-3 --input '{
  "prompt": "Tracking shot following a cyclist through busy city streets, morning rush hour, natural lighting, handheld camera feel, documentary style, authentic and candid"
}'
```

### Food Content

```bash
infsh app run bytedance/seedance-1-5-pro --input '{
  "prompt": "Close-up of chocolate sauce being drizzled over ice cream, slow motion, steam rising, soft lighting, food photography style, appetizing, commercial quality"
}'
```

### Tech/Futuristic

```bash
infsh app run xai/grok-imagine-video --input '{
  "prompt": "Futuristic control room with holographic displays, camera slowly pans across the space, blue and cyan lighting, sci-fi atmosphere, Blade Runner aesthetic, 4K",
  "duration": 5
}'
```

## Common Mistakes to Avoid

| Mistake | Problem | Better Approach |
|---------|---------|-----------------|
| Too vague | "A nice video" | Specify shot, subject, style |
| Too complex | Multiple scenes | One scene per prompt |
| No motion | Static description | Include camera movement or action |
| Conflicting styles | "Minimalist maximalist" | Choose one aesthetic |
| No lighting | Undefined mood | Specify lighting conditions |

## Model-Specific Tips

### Google Veo

- Excels at realistic, cinematic content
- Supports audio generation (Veo 3+)
- Best with detailed, professional prompts
- Frame interpolation available in 3.1

### Seedance

- Strong at dance and human motion
- First-frame control available
- Good for consistent character motion
- Works well with reference images

### Wan 2.5

- Best for image-to-video
- Animates still images naturally
- Good motion prediction
- Works with any image style

### Grok

- Good general-purpose video
- Configurable duration (5-10s)
- Creative interpretations
- Works well with abstract concepts

## Workflow: Iterative Prompting

```bash
# 1. Start with basic prompt
infsh app run google/veo-3-1-fast --input '{
  "prompt": "A woman walking through a forest"
}'

# 2. Add specificity
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Medium tracking shot of a woman in a red dress walking through an autumn forest"
}'

# 3. Add style and technical details
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Cinematic medium tracking shot of a woman in a flowing red dress walking through an autumn forest, golden hour sunlight filtering through leaves, shallow depth of field, film grain, 4K"
}'
```

## Related Skills

```bash
# Generate videos
npx skills add inference-sh/skills@ai-video-generation

# Google Veo specific
npx skills add inference-sh/skills@google-veo

# Generate images for image-to-video
npx skills add inference-sh/skills@ai-image-generation

# General prompt engineering
npx skills add inference-sh/skills@prompt-engineering

# Full platform skill
npx skills add inference-sh/skills@inference-sh
```

Browse all video apps: `infsh app list --category video`

## Documentation

- [Running Apps](https://inference.sh/docs/apps/running) - How to run apps via CLI
- [Video Generation Guide](https://inference.sh/blog/guides/video-generation) - Comprehensive video guide
