---
name: ai-content-pipeline
description: "Build multi-step AI content creation pipelines combining image, video, audio, and text. Workflow examples: generate image -> animate -> add voiceover -> merge with music. Tools: FLUX, Veo, Kokoro TTS, OmniHuman, media merger, upscaling. Use for: YouTube videos, social media content, marketing materials, automated content. Triggers: content pipeline, ai workflow, content creation, multi-step ai, content automation, ai video workflow, generate and edit, ai content factory, automated content creation, ai production pipeline, media pipeline, content at scale"
allowed-tools: Bash(infsh *)
---

# AI Content Pipeline

Build multi-step content creation pipelines via [inference.sh](https://inference.sh) CLI.

![AI Content Pipeline](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg06qgcg105rh6y1kvxm4wvm.png)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Simple pipeline: Generate image -> Animate to video
infsh app run falai/flux-dev --input '{"prompt": "portrait of a woman smiling"}' > image.json
infsh app run falai/wan-2-5 --input '{"image_url": "<url-from-previous>"}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Pipeline Patterns

### Pattern 1: Image -> Video -> Audio

```
[FLUX Image] -> [Wan 2.5 Video] -> [Foley Sound]
```

### Pattern 2: Script -> Speech -> Avatar

```
[LLM Script] -> [Kokoro TTS] -> [OmniHuman Avatar]
```

### Pattern 3: Research -> Content -> Distribution

```
[Tavily Search] -> [Claude Summary] -> [FLUX Visual] -> [Twitter Post]
```

## Complete Workflows

### YouTube Short Pipeline

Create a complete short-form video from a topic.

```bash
# 1. Generate script with Claude
infsh app run openrouter/claude-sonnet-45 --input '{
  "prompt": "Write a 30-second script about the future of AI. Make it engaging and conversational. Just the script, no stage directions."
}' > script.json

# 2. Generate voiceover with Kokoro
infsh app run infsh/kokoro-tts --input '{
  "text": "<script-text>",
  "voice": "af_sarah"
}' > voice.json

# 3. Generate background image with FLUX
infsh app run falai/flux-dev --input '{
  "prompt": "Futuristic city skyline at sunset, cyberpunk aesthetic, 4K wallpaper"
}' > background.json

# 4. Animate image to video with Wan
infsh app run falai/wan-2-5 --input '{
  "image_url": "<background-url>",
  "prompt": "slow camera pan across cityscape, subtle movement"
}' > video.json

# 5. Add captions (manually or with another tool)

# 6. Merge video with audio
infsh app run infsh/media-merger --input '{
  "video_url": "<video-url>",
  "audio_url": "<voice-url>"
}'
```

### Talking Head Video Pipeline

Create an AI avatar presenting content.

```bash
# 1. Write the script
infsh app run openrouter/claude-sonnet-45 --input '{
  "prompt": "Write a 1-minute explainer script about quantum computing for beginners."
}' > script.json

# 2. Generate speech
infsh app run infsh/kokoro-tts --input '{
  "text": "<script>",
  "voice": "am_michael"
}' > speech.json

# 3. Generate or use a portrait image
infsh app run falai/flux-dev --input '{
  "prompt": "Professional headshot of a friendly tech presenter, neutral background, looking at camera"
}' > portrait.json

# 4. Create talking head video
infsh app run bytedance/omnihuman-1-5 --input '{
  "image_url": "<portrait-url>",
  "audio_url": "<speech-url>"
}' > talking_head.json
```

### Product Demo Pipeline

Create a product showcase video.

```bash
# 1. Generate product image
infsh app run falai/flux-dev --input '{
  "prompt": "Sleek wireless earbuds on white surface, studio lighting, product photography"
}' > product.json

# 2. Animate product reveal
infsh app run falai/wan-2-5 --input '{
  "image_url": "<product-url>",
  "prompt": "slow 360 rotation, smooth motion"
}' > product_video.json

# 3. Upscale video quality
infsh app run falai/topaz-video-upscaler --input '{
  "video_url": "<product-video-url>"
}' > upscaled.json

# 4. Add background music
infsh app run infsh/media-merger --input '{
  "video_url": "<upscaled-url>",
  "audio_url": "https://your-music.mp3",
  "audio_volume": 0.3
}'
```

### Blog to Video Pipeline

Convert written content to video format.

```bash
# 1. Summarize blog post
infsh app run openrouter/claude-haiku-45 --input '{
  "prompt": "Summarize this blog post into 5 key points for a video script: <blog-content>"
}' > summary.json

# 2. Generate images for each point
for i in 1 2 3 4 5; do
  infsh app run falai/flux-dev --input "{
    \"prompt\": \"Visual representing point $i: <point-text>\"
  }" > "image_$i.json"
done

# 3. Animate each image
for i in 1 2 3 4 5; do
  infsh app run falai/wan-2-5 --input "{
    \"image_url\": \"<image-$i-url>\"
  }" > "video_$i.json"
done

# 4. Generate voiceover
infsh app run infsh/kokoro-tts --input '{
  "text": "<full-script>",
  "voice": "bf_emma"
}' > narration.json

# 5. Merge all clips
infsh app run infsh/media-merger --input '{
  "videos": ["<video1>", "<video2>", "<video3>", "<video4>", "<video5>"],
  "audio_url": "<narration-url>",
  "transition": "crossfade"
}'
```

## Pipeline Building Blocks

### Content Generation

| Step | App | Purpose |
|------|-----|---------|
| Script | `openrouter/claude-sonnet-45` | Write content |
| Research | `tavily/search-assistant` | Gather information |
| Summary | `openrouter/claude-haiku-45` | Condense content |

### Visual Assets

| Step | App | Purpose |
|------|-----|---------|
| Image | `falai/flux-dev` | Generate images |
| Image | `google/imagen-3` | Alternative image gen |
| Upscale | `falai/topaz-image-upscaler` | Enhance quality |

### Animation

| Step | App | Purpose |
|------|-----|---------|
| I2V | `falai/wan-2-5` | Animate images |
| T2V | `google/veo-3-1-fast` | Generate from text |
| Avatar | `bytedance/omnihuman-1-5` | Talking heads |

### Audio

| Step | App | Purpose |
|------|-----|---------|
| TTS | `infsh/kokoro-tts` | Voice narration |
| Music | `infsh/ai-music` | Background music |
| Foley | `infsh/hunyuanvideo-foley` | Sound effects |

### Post-Production

| Step | App | Purpose |
|------|-----|---------|
| Upscale | `falai/topaz-video-upscaler` | Enhance video |
| Merge | `infsh/media-merger` | Combine media |
| Caption | `infsh/caption-video` | Add subtitles |

## Best Practices

1. **Plan the pipeline first** - Map out each step before running
2. **Save intermediate results** - Store outputs for iteration
3. **Use appropriate quality** - Fast models for drafts, quality for finals
4. **Match resolutions** - Keep consistent aspect ratios throughout
5. **Test each step** - Verify outputs before proceeding

## Related Skills

```bash
# Video generation models
npx skills add inference-sh/skills@ai-video-generation

# Image generation
npx skills add inference-sh/skills@ai-image-generation

# Text-to-speech
npx skills add inference-sh/skills@text-to-speech

# LLM models for scripts
npx skills add inference-sh/skills@llm-models

# Full platform skill
npx skills add inference-sh/skills@inference-sh
```

Browse all apps: `infsh app list`

## Documentation

- [Content Pipeline Example](https://inference.sh/docs/examples/content-pipeline) - Official pipeline guide
- [Building Workflows](https://inference.sh/blog/guides/ai-workflows) - Workflow best practices
