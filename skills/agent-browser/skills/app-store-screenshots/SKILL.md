---
name: app-store-screenshots
description: "App Store and Google Play screenshot creation with exact platform specs. Covers iOS/Android dimensions, gallery ordering, device mockups, and preview videos. Use for: app store optimization, ASO, app screenshots, app preview, play store listing. Triggers: app store screenshots, aso, app store optimization, play store screenshots, app preview, app listing, ios screenshots, android screenshots, app store images, app mockup, device mockup, app gallery, store listing"
allowed-tools: Bash(infsh *)
---

# App Store Screenshots

Create app store screenshots and preview videos via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a device mockup scene
infsh app run falai/flux-dev-lora --input '{
  "prompt": "iPhone 15 Pro showing a clean modern app interface with analytics dashboard, floating at slight angle, soft gradient background, professional product photography, subtle shadow, marketing mockup style",
  "width": 1024,
  "height": 1536
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Platform Specifications

### Apple App Store (iOS)

| Device | Dimensions (px) | Required |
|--------|-----------------|----------|
| iPhone 6.7" (15 Pro Max) | 1290 x 2796 | Required |
| iPhone 6.5" (11 Pro Max) | 1284 x 2778 | Required |
| iPhone 5.5" (8 Plus) | 1242 x 2208 | Optional |
| iPad Pro 12.9" (6th gen) | 2048 x 2732 | If iPad app |
| iPad Pro 11" | 1668 x 2388 | If iPad app |

- Up to **10 screenshots** per localization
- First **3 screenshots** are visible without scrolling (critical)
- Formats: PNG or JPEG (no alpha/transparency for JPEG)

### Google Play Store (Android)

| Spec | Value |
|------|-------|
| Min dimensions | 320 px (any side) |
| Max dimensions | 3840 px (any side) |
| Aspect ratio | 16:9 or 9:16 |
| Max screenshots | 8 per device type |
| Formats | PNG or JPEG (24-bit, no alpha) |

- Feature graphic: 1024 x 500 px (required for featuring)
- Promo video: YouTube URL (optional but recommended)

## The First 3 Rule

**80% of App Store impressions show only the first 3 screenshots** (before the user scrolls). These three must:

1. Communicate the core value proposition
2. Show the best feature/outcome
3. Differentiate from competitors

### Screenshot Gallery Order

| Position | Content | Purpose |
|----------|---------|---------|
| **1** | Hero — core value, best feature | Stop the scroll, communicate what the app does |
| **2** | Key differentiator | What makes you unique vs competitors |
| **3** | Most popular feature | The thing users love most |
| **4** | Social proof or outcome | Ratings, results, testimonials |
| **5-8** | Additional features | Supporting features, settings, integrations |
| **9-10** | Edge cases | Specialized features for niche users |

## Screenshot Styles

### 1. Device Frame with Caption

The standard: device mockup showing the app, caption text above/below.

```
┌──────────────────────────┐
│   "Track Your Habits     │  ← Caption (benefit-focused)
│    Effortlessly"         │
│                          │
│   ┌──────────────────┐   │
│   │                  │   │
│   │   App Screen     │   │  ← Actual app UI in device frame
│   │   Content        │   │
│   │                  │   │
│   │                  │   │
│   └──────────────────┘   │
│                          │
└──────────────────────────┘
```

### 2. Full-Bleed UI (No Device Frame)

The app UI fills the entire screenshot. Works for immersive apps.

### 3. Lifestyle Context

The device shown in a real-world context (person holding phone, on desk, etc.).

### 4. Feature Highlight with Callouts

UI screenshot with arrows/circles pointing to specific features.

## Caption Writing

### Rules

- **Max 2 lines** of text
- **Benefit-focused**, not feature-focused
- **30pt+ equivalent** font size (must be readable in store)

### Examples

```
❌ Feature-focused:
"Push Notification System"
"Calendar View with Filters"
"Data Export Functionality"

✅ Benefit-focused:
"Never Miss a Deadline Again"
"See Your Week at a Glance"
"Share Reports in One Tap"
```

## Generating Screenshots

### Hero Screenshot (Position 1)

```bash
# Clean device mockup with hero feature
infsh app run falai/flux-dev-lora --input '{
  "prompt": "modern iPhone showing a beautiful fitness tracking app with activity rings and workout summary, device floating at slight angle against soft purple gradient background, professional product shot, clean minimal composition, subtle reflection",
  "width": 1024,
  "height": 1536
}'
```

### Feature Highlight

```bash
# Feature callout style
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "app store screenshot style, iPhone showing a messaging app with AI writing suggestions highlighted, clean white background, subtle UI callout arrows, professional marketing asset, modern design",
  "size": "2K"
}'
```

### Lifestyle Context

```bash
# Device in real-world setting
infsh app run falai/flux-dev-lora --input '{
  "prompt": "person holding iPhone showing a cooking recipe app, kitchen background with ingredients, warm natural lighting, over-the-shoulder perspective, lifestyle photography, authentic feeling",
  "width": 1024,
  "height": 1536
}'
```

### Before/After

```bash
# Split comparison
infsh app run infsh/stitch-images --input '{
  "images": ["before-screenshot.png", "after-screenshot.png"],
  "direction": "horizontal"
}'
```

## Preview Videos

### Apple App Store

| Spec | Value |
|------|-------|
| Duration | 15-30 seconds |
| Orientation | Portrait or landscape (match app) |
| Audio | Optional (loops silently in store) |
| Format | H.264, .mov or .mp4 |

### Google Play

| Spec | Value |
|------|-------|
| Source | YouTube URL |
| Duration | 30s-2min recommended |
| Orientation | Landscape preferred |

### Preview Video Structure

| Segment | Duration | Content |
|---------|----------|---------|
| Hook | 0-3s | Show the core outcome/wow moment |
| Feature 1 | 3-10s | Demonstrate top feature in action |
| Feature 2 | 10-18s | Second key feature |
| Feature 3 | 18-25s | Third feature or social proof |
| CTA | 25-30s | End screen with app icon |

```bash
# Generate preview video scenes
infsh app run google/veo-3-1-fast --input '{
  "prompt": "smooth screen recording style, finger tapping on a modern mobile app interface, swiping between screens showing charts and data visualizations, clean UI transitions, professional app demo"
}'
```

## Localization

Each language gets its own set of screenshots. Priorities:

| Market | Localization Level |
|--------|-------------------|
| Primary markets | Full: new screenshots + translated captions |
| Secondary markets | Translated captions, same screenshots |
| Other | English defaults |

Key localization markets: English, Japanese, Korean, Chinese (Simplified), German, French, Spanish, Portuguese (Brazilian)

## A/B Testing (Google Play)

Google Play Console supports store listing experiments:

- Test different screenshot orders
- Test with/without device frames
- Test different captions
- Test different color schemes
- Run for 7+ days with 50%+ traffic for significant results

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Settings screen as screenshot | Nobody cares about settings | Show core value, not infrastructure |
| Onboarding flow screenshots | Shows friction, not value | Show the app in-use state |
| Too much text | Unreadable in store | Max 2 lines, 30pt+ font |
| Wrong dimensions | Rejected by store | Use exact platform specs |
| All screenshots look the same | No reason to scroll | Vary composition and content |
| Feature-focused captions | Doesn't communicate benefit | "Never Miss a Deadline" > "Push Notifications" |
| Outdated UI | Looks abandoned | Update screenshots with each major release |
| No hero screenshot | Weak first impression | Position 1 = your best shot |

## Checklist

- [ ] Correct dimensions for target platform
- [ ] First 3 screenshots communicate core value
- [ ] Captions are benefit-focused, max 2 lines
- [ ] No onboarding or settings screens
- [ ] Preview video is 15-30s with hook in first 3s
- [ ] Localized for top markets
- [ ] Feature graphic (1024x500) for Google Play
- [ ] Screenshots updated for current app version
- [ ] A/B test variant prepared

## Related Skills

```bash
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@ai-video-generation
npx skills add inference-sh/skills@image-upscaling
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
