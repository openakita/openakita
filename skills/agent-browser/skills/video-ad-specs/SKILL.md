---
name: video-ad-specs
description: "Video ad creation with exact platform-specific specs for TikTok, Instagram, YouTube, Facebook, LinkedIn. Covers dimensions, duration limits, AIDA framework, and caption requirements. Use for: video ads, social media ads, paid media creative, video marketing, ad production. Triggers: video ad, social media ad, tiktok ad, instagram ad, youtube ad, facebook ad, linkedin ad, video creative, ad specs, paid media, video marketing, ad production, reels ad, stories ad, pre roll, bumper ad"
allowed-tools: Bash(infsh *)
---

# Video Ad Specs

Create platform-specific video ads via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a vertical video ad scene
infsh app run bytedance/seedance-1-5-pro --input '{
  "prompt": "vertical video, person excitedly unboxing a product, clean modern room, bright natural lighting, social media ad style, authentic feeling, 9:16 format"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Platform Specifications

### TikTok

| Spec | Value |
|------|-------|
| Aspect ratio | **9:16** (vertical) |
| Resolution | 1080 x 1920 px |
| Duration | 5-60 seconds (15-30s recommended) |
| File size | Max 500 MB |
| Format | MP4, MOV |
| Sound | On by default (design with sound) |
| Text safe zone | 150px from all edges |
| Hook window | **1 second** — first frame must grab attention |

### Instagram Reels

| Spec | Value |
|------|-------|
| Aspect ratio | **9:16** (vertical) |
| Resolution | 1080 x 1920 px |
| Duration | Up to 90 seconds (15-30s for ads) |
| Cover image | Separate upload, shows in grid |
| Sound | On by default |
| Caption area | Bottom 20% reserved for text overlay |

### Instagram Stories

| Spec | Value |
|------|-------|
| Aspect ratio | **9:16** |
| Resolution | 1080 x 1920 px |
| Duration | Up to 15 seconds per segment |
| Swipe-up/Link | Available for ads |
| Top/bottom | 14% top and 20% bottom = unsafe for key content |

### YouTube

| Format | Aspect | Duration | Skip |
|--------|--------|----------|------|
| Bumper | 16:9 | **6 seconds** exactly | Non-skippable |
| Non-skippable | 16:9 | **15 seconds** | Non-skippable |
| Skippable (TrueView) | 16:9 | Any length | Skip after **5 seconds** |
| Shorts | 9:16 | Up to 60 seconds | N/A |

Resolution: 1920 x 1080 (16:9) or 1080 x 1920 (Shorts)

### Facebook Feed

| Spec | Value |
|------|-------|
| Aspect ratio | **1:1** (square) or **4:5** (recommended for mobile) |
| Resolution | 1080 x 1080 or 1080 x 1350 |
| Duration | Up to 240 min (15-30s recommended) |
| Autoplay | **Silent** — captions are essential |
| Sound | 85% of Facebook video is watched **without sound** |

### LinkedIn

| Spec | Value |
|------|-------|
| Aspect ratio | **1:1** or **16:9** |
| Resolution | 1080 x 1080 or 1920 x 1080 |
| Duration | 3 seconds to 10 minutes (15-30s for ads) |
| Tone | Professional |
| Autoplay | Silent in feed |

## AIDA Framework for Video Ads

| Phase | Time | Goal | Technique |
|-------|------|------|-----------|
| **Attention** | 0-3s | Stop the scroll | Pattern interrupt, bold visual, question |
| **Interest** | 3-10s | Keep watching | State the problem, show relevance |
| **Desire** | 10-20s | Want the solution | Show the product/outcome, social proof |
| **Action** | Final 3-5s | Click/buy/sign up | Clear CTA, urgency, offer |

### Hook Techniques (First 3 Seconds)

| Technique | Example |
|-----------|---------|
| Bold statement | "This tool replaced my entire marketing team" |
| Question | "Why are you still doing this manually?" |
| Surprising visual | Unexpected transformation, before/after reveal |
| Pattern interrupt | Start mid-action, unusual angle, bright color |
| Social proof | "2 million people switched to this" |
| Pain point | "If you hate [common frustration], watch this" |

## Creating Video Ads

### Vertical (TikTok, Reels, Stories, Shorts)

```bash
# Hook scene (0-3s)
infsh app run google/veo-3-1-fast --input '{
  "prompt": "vertical 9:16 video, close-up of hands struggling with tangled cables and messy desk, frustrated energy, shaky handheld camera, authentic social media style, bright lighting"
}'

# Solution reveal (3-15s)
infsh app run bytedance/seedance-1-5-pro --input '{
  "prompt": "vertical video, smooth product reveal, clean wireless charging station on minimalist desk, satisfying organization transformation, bright modern room, social media ad aesthetic"
}'

# Add voiceover
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] Stop wasting time with this mess. This one product changed my entire setup. Everything charges. Everything is organized. Link in bio."
}'

# Merge video + audio
infsh app run infsh/video-audio-merger --input '{
  "video": "solution-reveal.mp4",
  "audio": "voiceover.mp3"
}'

# Add captions (critical for silent autoplay)
infsh app run infsh/caption-videos --input '{
  "video": "ad-with-audio.mp4",
  "caption_file": "captions.srt"
}'
```

### Square (Facebook, LinkedIn Feed)

```bash
infsh app run google/veo-3-1-fast --input '{
  "prompt": "square 1:1 video, professional person at desk discovering a new software tool, laptop screen showing clean dashboard, natural office lighting, corporate commercial style, satisfied expression"
}'
```

### YouTube Bumper (6 Seconds)

```bash
# 6-second bumper: one message, one visual, one CTA
infsh app run google/veo-3-1-fast --input '{
  "prompt": "6 second product ad, quick montage of a sleek app being used on phone, fast cuts, modern, energetic, brand logo reveal at end, punchy and dynamic, wide 16:9"
}'

# Keep it tight
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] Your reports. Automated. Try DataFlow free."
}'
```

## Captions Are Mandatory

85% of Facebook and 40%+ of Instagram video is watched on mute.

### Caption Best Practices

| Rule | Reason |
|------|--------|
| Always add captions | Silent viewing is the default on most platforms |
| Large, readable font | Small text is invisible on mobile |
| High contrast | White text with dark outline/background |
| Centered or bottom-third | Standard viewing position |
| Max 2 lines at a time | More text = can't be read fast enough |
| Key words in bold/color | Draws eye to important words |

```bash
# Generate captions from audio
# (create SRT file from your script, then burn in)
infsh app run infsh/caption-videos --input '{
  "video": "ad-video.mp4",
  "caption_file": "ad-captions.srt"
}'
```

## Ad Structure Templates

### Testimonial Ad (15-30s)

| Time | Content |
|------|---------|
| 0-3s | Customer states the problem they had |
| 3-15s | How they discovered and tried the product |
| 15-25s | The specific result they achieved |
| 25-30s | Product name + CTA |

### Demo Ad (15-30s)

| Time | Content |
|------|---------|
| 0-3s | The problem (text or visual) |
| 3-20s | Product demo showing the solution |
| 20-25s | Key result/benefit |
| 25-30s | CTA + offer |

### Before/After Ad (15s)

| Time | Content |
|------|---------|
| 0-3s | "Before" state (messy, slow, frustrating) |
| 3-5s | Transition / product introduction |
| 5-12s | "After" state (clean, fast, satisfying) |
| 12-15s | CTA |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| No hook in first 1-3s | Viewer scrolls past | Open with pattern interrupt |
| Landscape video on TikTok/Reels | Letterboxed, looks amateur | Use 9:16 for vertical platforms |
| No captions | Most viewers watch silent | Always add captions |
| CTA too late | Viewers already left | Clear CTA within last 5 seconds |
| Too long for platform | Forced skip or dropout | Match platform duration norms |
| Same ad for all platforms | Wrong specs, wrong tone | Create platform-specific versions |
| Logo in first 3s | Feels like a commercial, gets skipped | Save branding for the end |
| Text in unsafe zones | Cut off by platform UI | Check safe zone per platform |

## Checklist

- [ ] Correct aspect ratio for target platform
- [ ] Hook in first 1-3 seconds
- [ ] Captions added (readable, high contrast)
- [ ] CTA clear and within final 5 seconds
- [ ] Duration matches platform norms
- [ ] Text outside platform unsafe zones
- [ ] Audio designed for both sound-on and sound-off
- [ ] Platform-specific version (not one-size-fits-all)

## Related Skills

```bash
npx skills add inference-sh/skills@ai-video-generation
npx skills add inference-sh/skills@video-prompting-guide
npx skills add inference-sh/skills@text-to-speech
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
