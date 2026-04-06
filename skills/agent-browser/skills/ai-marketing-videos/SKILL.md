---
name: ai-marketing-videos
description: "Create AI marketing videos for ads, promos, product launches, and brand content. Models: Veo, Seedance, Wan, FLUX for visuals, Kokoro for voiceover. Types: product demos, testimonials, explainers, social ads, brand videos. Use for: Facebook ads, YouTube ads, product launches, brand awareness. Triggers: marketing video, ad video, promo video, commercial, brand video, product video, explainer video, ad creative, video ad, facebook ad video, youtube ad, instagram ad, tiktok ad, promotional video, launch video"
allowed-tools: Bash(infsh *)
---

# AI Marketing Videos

Create professional marketing videos via [inference.sh](https://inference.sh) CLI.

![AI Marketing Videos](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg2c0egyg243mnyth4y6g51q.jpeg)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a product promo video
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Sleek product reveal video, smartphone emerging from light particles, premium tech aesthetic, commercial quality"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Video Ad Types

| Type | Duration | Platform |
|------|----------|----------|
| Bumper Ad | 6 seconds | YouTube |
| Short Ad | 15 seconds | Instagram, Facebook |
| Standard Ad | 30 seconds | YouTube, TV |
| Explainer | 60-90 seconds | Website, YouTube |
| Product Demo | 30-60 seconds | All platforms |

## Marketing Video Templates

### Product Launch

```bash
# Dramatic product reveal
infsh app run google/veo-3 --input '{
  "prompt": "Cinematic product launch video, premium tech device floating in space, dramatic lighting, particles and light effects, Apple-style reveal, commercial quality"
}'
```

### Brand Story

```bash
# Emotional brand narrative
infsh app run google/veo-3-1 --input '{
  "prompt": "Brand story video showing diverse people connecting through technology, warm color grading, lifestyle montage, emotional and inspiring, commercial"
}'
```

### Feature Highlight

```bash
# Focus on specific feature
infsh app run bytedance/seedance-1-5-pro --input '{
  "prompt": "Close-up product feature demonstration, hands interacting with device, clean background, informative, tech commercial style"
}'
```

### Testimonial Style

```bash
# Talking head testimonial
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Customer testimonial style video, person speaking to camera, neutral office background, professional lighting, authentic feel"
}'
```

### Before/After

```bash
# Transformation reveal
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Before and after transformation video, split screen transition, dramatic reveal, satisfying comparison, commercial style"
}'
```

## Complete Ad Workflows

### 30-Second Product Ad

```bash
# 1. Opening hook (0-3s)
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Attention-grabbing opening, product silhouette in dramatic lighting, building anticipation"
}' > hook.json

# 2. Problem statement (3-8s)
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Frustrated person dealing with common problem, relatable everyday situation, documentary style"
}' > problem.json

# 3. Solution reveal (8-15s)
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Product reveal with features highlighted, clean demonstration, solving the problem shown before"
}' > solution.json

# 4. Benefits showcase (15-25s)
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Happy customer using product, lifestyle integration, multiple quick cuts showing benefits"
}' > benefits.json

# 5. Call to action (25-30s)
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Product hero shot with space for text overlay, professional lighting, commercial ending"
}' > cta.json

# 6. Generate voiceover
infsh app run infsh/kokoro-tts --input '{
  "text": "Tired of [problem]? Introducing [Product]. [Key benefit 1]. [Key benefit 2]. [Key benefit 3]. Get yours today.",
  "voice": "af_nicole"
}' > voiceover.json

# 7. Merge all clips with voiceover
infsh app run infsh/media-merger --input '{
  "videos": ["<hook>", "<problem>", "<solution>", "<benefits>", "<cta>"],
  "audio_url": "<voiceover>",
  "transition": "crossfade"
}'
```

### Instagram/TikTok Ad (15s)

```bash
# Vertical format, fast-paced
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Fast-paced product showcase, vertical 9:16, quick cuts, trending style, hook in first 2 seconds, satisfying visually, Gen-Z aesthetic"
}'

# Add trendy music
infsh app run infsh/media-merger --input '{
  "video_url": "<video>",
  "audio_url": "https://trending-music.mp3"
}'
```

### Explainer Video

```bash
# 1. Write script
infsh app run openrouter/claude-sonnet-45 --input '{
  "prompt": "Write a 60-second explainer video script for a SaaS product. Include: hook, problem, solution, 3 key features, social proof, CTA. Make it conversational."
}' > script.json

# 2. Generate visuals for each section
SECTIONS=("hook" "problem" "solution" "feature1" "feature2" "feature3" "social_proof" "cta")

for section in "${SECTIONS[@]}"; do
  infsh app run google/veo-3-1-fast --input "{
    \"prompt\": \"Explainer video scene for $section, motion graphics style, clean modern aesthetic, SaaS product\"
  }" > "$section.json"
done

# 3. Generate professional voiceover
infsh app run infsh/kokoro-tts --input '{
  "text": "<full-script>",
  "voice": "am_michael"
}' > voiceover.json

# 4. Assemble final video
infsh app run infsh/media-merger --input '{
  "videos": ["<hook>", "<problem>", "<solution>", ...],
  "audio_url": "<voiceover>",
  "transition": "fade"
}'
```

## Platform-Specific Formats

### Facebook/Instagram Feed

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Square format product video 1:1, eye-catching visuals, works without sound, text-friendly, scroll-stopping"
}'
```

### YouTube Pre-Roll

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "YouTube ad style, skip button awareness (hook in 5 seconds), 16:9, professional commercial quality"
}'
```

### LinkedIn

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Professional B2B product video, corporate style, clean and modern, business audience, subtle motion"
}'
```

### TikTok/Reels

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "TikTok native style ad, vertical 9:16, raw authentic feel, not overly polished, trendy, user-generated content aesthetic"
}'
```

## Ad Creative Best Practices

### Hook Formula (First 3 Seconds)

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Opening hook: [choose one]
  - Surprising visual transformation
  - Bold statement text animation
  - Relatable problem scenario
  - Curiosity gap visual
  - Satisfying action"
}'
```

### Visual Hierarchy

1. **Product hero** - Clear, prominent
2. **Benefits** - Illustrated, not just stated
3. **Social proof** - Visible testimonials/numbers
4. **CTA** - Clear space for text overlay

### Sound Design

```bash
# Add appropriate music
infsh app run infsh/ai-music --input '{
  "prompt": "Upbeat commercial background music, modern, energetic, 30 seconds"
}' > music.json

infsh app run infsh/media-merger --input '{
  "video_url": "<ad-video>",
  "audio_url": "<music>",
  "audio_volume": 0.5
}'
```

## A/B Testing Variants

```bash
# Generate multiple creative variants
HOOKS=(
  "Problem-focused opening"
  "Product reveal opening"
  "Testimonial opening"
  "Statistic opening"
)

for hook in "${HOOKS[@]}"; do
  infsh app run google/veo-3-1-fast --input "{
    \"prompt\": \"Marketing video with $hook, professional commercial quality\"
  }" > "variant_${hook// /_}.json"
done
```

## Video Ad Checklist

- [ ] Hook in first 3 seconds
- [ ] Works without sound (captions/text)
- [ ] Clear product visibility
- [ ] Benefit-focused messaging
- [ ] Single clear CTA
- [ ] Correct aspect ratio for platform
- [ ] Brand consistency
- [ ] Mobile-optimized

## Related Skills

```bash
# Video generation
npx skills add inference-sh/skills@ai-video-generation

# Image generation for thumbnails
npx skills add inference-sh/skills@ai-image-generation

# Text-to-speech for voiceover
npx skills add inference-sh/skills@text-to-speech

# Social media content
npx skills add inference-sh/skills@ai-social-media-content

# Full platform skill
npx skills add inference-sh/skills@inference-sh
```

Browse all apps: `infsh app list`
