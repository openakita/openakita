---
name: explainer-video-guide
description: "Explainer video production guide: scripting, voiceover, visuals, and assembly. Covers script formulas, pacing rules, scene planning, and multi-tool pipelines. Use for: product demos, how-it-works videos, onboarding videos, social explainers. Triggers: explainer video, how to make explainer, product video, demo video, video production, video script, animated explainer, product demo video, tutorial video, onboarding video, walkthrough video, video pipeline"
allowed-tools: Bash(infsh *)
---

# Explainer Video Guide

Create explainer videos from script to final cut via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a scene for an explainer
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Clean motion graphics style animation, abstract data flowing between connected nodes, blue and white color scheme, professional corporate aesthetic, smooth transitions"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Script Formulas

### Problem-Agitate-Solve (PAS) — 60 seconds

| Section | Duration | Content | Word Count |
|---------|----------|---------|------------|
| **Problem** | 10s | State the pain point the viewer has | ~25 words |
| **Agitate** | 10s | Show why it's worse than they think | ~25 words |
| **Solution** | 15s | Introduce your product/idea | ~35 words |
| **How It Works** | 20s | Show 3 key steps or features | ~50 words |
| **CTA** | 5s | One clear next action | ~12 words |

### Before-After-Bridge (BAB) — 90 seconds

| Section | Duration | Content |
|---------|----------|---------|
| **Before** | 15s | Show the current frustrating state |
| **After** | 15s | Show the ideal outcome |
| **Bridge** | 40s | Explain how your product gets them there |
| **Social Proof** | 10s | Quick stat or testimonial |
| **CTA** | 10s | Clear next step |

### Feature Spotlight — 30 seconds (social)

| Section | Duration | Content |
|---------|----------|---------|
| **Hook** | 3s | Surprising fact or question |
| **Feature** | 15s | Show one feature solving one problem |
| **Result** | 7s | The outcome/benefit |
| **CTA** | 5s | Try it / Learn more |

## Pacing Rules

| Content Type | Words Per Minute | Notes |
|-------------|-----------------|-------|
| Standard narration | 150 wpm | Conversational pace |
| Complex/technical | 120 wpm | Allow processing time |
| Energetic/social | 170 wpm | Faster for short-form |
| Children's content | 100 wpm | Clear and slow |

**Key rule:** 1 scene per key message. Don't pack multiple ideas into one visual.

### Scene Duration Guidelines

- Establishing shot: 3-5 seconds
- Feature demonstration: 5-8 seconds
- Text/stat on screen: 3-4 seconds (must be readable)
- Transition: 0.5-1 second
- CTA screen: 3-5 seconds

## Visual Production

### Scene Types

```bash
# Product in context
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Clean product demonstration video, hands typing on a laptop showing a dashboard interface, bright modern office, soft natural lighting, professional"
}'

# Abstract concept visualization
infsh app run bytedance/seedance-1-5-pro --input '{
  "prompt": "Abstract motion graphics, colorful data streams connecting floating geometric shapes, smooth fluid animation, dark background with glowing elements, tech aesthetic"
}'

# Lifestyle/outcome shot
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Happy person relaxing on couch with laptop, smiling at screen, bright airy living room, warm afternoon light, satisfied customer feeling, lifestyle commercial style"
}'

# Before/after comparison
infsh app run falai/flux-dev-lora --input '{
  "prompt": "Split screen comparison, left side cluttered messy desk with papers and stress, right side clean organized minimalist workspace, dramatic difference, clean design"
}'
```

### Image-to-Video for Scenes

```bash
# Generate a still frame first
infsh app run falai/flux-dev-lora --input '{
  "prompt": "Professional workspace with glowing holographic interface, futuristic but clean, blue accent lighting"
}'

# Animate it
infsh app run falai/wan-2-5-i2v --input '{
  "prompt": "Gentle camera push in, holographic elements subtly floating and rotating, soft ambient light shifts",
  "image": "path/to/workspace-still.png"
}'
```

## Voiceover Production

### Script Writing Tips

- Short sentences. Max 15 words per sentence.
- Active voice. "You can track your data" not "Your data can be tracked."
- Conversational tone. Read it aloud — if it sounds stiff, rewrite.
- One idea per sentence. One sentence per visual beat.

### Generating Voiceover

```bash
# Professional narration with Dia TTS
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] Tired of spending hours on reports that nobody reads? There is a better way. Meet DataFlow. It turns your raw data into visual stories... in seconds. Just connect your source, pick a template, and share. Try DataFlow free today."
}'
```

### Pacing Control in TTS

| Technique | Effect | Example |
|-----------|--------|---------|
| Period `.` | Medium pause | "This changes everything. Here's how." |
| Ellipsis `...` | Long pause (dramatic) | "And the result... was incredible." |
| Comma `,` | Short pause | "Fast, simple, powerful." |
| Exclamation `!` | Emphasis/energy | "Start building today!" |
| Question `?` | Rising intonation | "What if there was a better way?" |

## Music & Audio

### Background Music Guidelines

- **Volume:** 20-30% under narration (duck 6-12dB when voice plays)
- **Style:** match the brand tone (corporate = ambient electronic, startup = upbeat indie)
- **Structure:** intro swell (first 3s) -> subtle loop under narration -> swell at CTA
- **No vocals:** instrumental only under narration

```bash
# Generate background music
infsh app run <music-gen-app> --input '{
  "prompt": "upbeat corporate background music, modern electronic, 90 BPM, positive and professional, no vocals, suitable for product explainer video"
}'
```

## Assembly Pipeline

### Full Production Workflow

```bash
# 1. Generate voiceover
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] Your script here..."
}'

# 2. Generate scene visuals (in parallel)
infsh app run google/veo-3-1-fast --input '{"prompt": "scene 1 description"}' --no-wait
infsh app run google/veo-3-1-fast --input '{"prompt": "scene 2 description"}' --no-wait
infsh app run google/veo-3-1-fast --input '{"prompt": "scene 3 description"}' --no-wait

# 3. Merge scenes into sequence
infsh app run infsh/media-merger --input '{
  "media": ["scene1.mp4", "scene2.mp4", "scene3.mp4"]
}'

# 4. Add voiceover to video
infsh app run infsh/video-audio-merger --input '{
  "video": "merged-scenes.mp4",
  "audio": "voiceover.mp3"
}'

# 5. Add captions
infsh app run infsh/caption-videos --input '{
  "video": "final-with-audio.mp4",
  "caption_file": "captions.srt"
}'
```

## Video Length by Format

| Format | Length | Platform |
|--------|--------|----------|
| Social teaser | 15-30s | TikTok, Instagram Reels, YouTube Shorts |
| Product demo | 60-90s | Website, landing page |
| Feature explainer | 90-120s | YouTube, email |
| Tutorial/walkthrough | 2-5min | YouTube, help center |
| Investor pitch video | 2-3min | Pitch deck supplement |

## Transition Types

| Transition | When to Use | Effect |
|------------|-------------|--------|
| **Cut** | Default between related scenes | Clean, professional |
| **Dissolve/Crossfade** | Time passing, mood shift | Soft, contemplative |
| **Wipe** | New topic or section | Clear separation |
| **Zoom/Push** | Drilling into detail | Focus attention |
| **Match cut** | Visual similarity between scenes | Clever, memorable |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Script too wordy | Voiceover rushed, viewer overwhelmed | Cut to 150 wpm max |
| No hook in first 3s | Viewers leave immediately | Start with the problem or surprising stat |
| Visuals lag narration | Confusing disconnect | Visuals should match or slightly precede words |
| Background music too loud | Can't hear narration | Duck music 6-12dB under voice |
| No captions | 85% of social video watched silent | Always add captions |
| Too many ideas | Viewer retains nothing | One core message per video |

## Related Skills

```bash
npx skills add inference-sh/skills@ai-video-generation
npx skills add inference-sh/skills@video-prompting-guide
npx skills add inference-sh/skills@text-to-speech
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
