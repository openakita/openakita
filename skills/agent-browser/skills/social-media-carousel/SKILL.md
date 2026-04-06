---
name: social-media-carousel
description: "Multi-slide carousel design for Instagram, LinkedIn, and Twitter/X with layout rules and hooks. Covers slide structure, text hierarchy, swipe psychology, and platform-specific specs. Use for: carousel posts, Instagram carousels, LinkedIn carousels, slide posts, educational content. Triggers: carousel, instagram carousel, linkedin carousel, slide post, carousel design, swipe post, multi-image post, carousel template, educational carousel, carousel content, instagram slides, linkedin slides"
allowed-tools: Bash(infsh *)
---

# Social Media Carousel

Design high-engagement carousel posts via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a carousel slide
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1080px;height:1080px;background:#0f172a;display:flex;align-items:center;justify-content:center;padding:80px;font-family:system-ui;color:white;text-align:center\"><div><p style=\"font-size:24px;color:#818cf8;text-transform:uppercase;letter-spacing:3px\">5 Rules for</p><h1 style=\"font-size:64px;margin:16px 0;font-weight:900;line-height:1.1\">Writing Headlines That Convert</h1><p style=\"font-size:22px;opacity:0.5;margin-top:24px\">Swipe →</p></div></div>"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Platform Specs

| Platform | Dimensions | Slides | Aspect Ratios |
|----------|-----------|--------|---------------|
| **Instagram** | 1080 x 1080 px | Up to 20 | 1:1 (default), 4:5, 16:9 |
| **LinkedIn** | 1080 x 1080 px or 1080 x 1350 | Up to 20 | 1:1, 4:5 |
| **Twitter/X** | 1080 x 1080 px | Up to 4 | 1:1, 16:9 |
| **Facebook** | 1080 x 1080 px | Up to 10 | 1:1, 4:5 |

**Use 1080 x 1350 (4:5)** on Instagram and LinkedIn — takes up more screen real estate in the feed than square.

## Carousel Structure

### The 7-Slide Framework

| Slide | Purpose | Content |
|-------|---------|---------|
| 1 | **Hook** | Bold claim, question, or promise — stops the scroll |
| 2 | **Context** | Why this matters, set up the problem |
| 3-6 | **Value** | One point per slide, numbered |
| 7 | **CTA** | Follow, save, share, comment, visit link |

### Slide 1: The Hook

The most important slide. If this fails, nobody swipes.

| Hook Type | Example |
|-----------|---------|
| Bold claim | "90% of landing pages make this mistake" |
| Question | "Why do your ads get clicks but no conversions?" |
| Number + promise | "7 Python tricks I wish I learned sooner" |
| Contrarian | "Stop writing blog posts (do this instead)" |
| Before/after | Show transformation |

```bash
# Hook slide
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1080px;height:1350px;background:linear-gradient(180deg,#1e1b4b,#312e81);display:flex;align-items:center;justify-content:center;padding:80px;font-family:system-ui;color:white;text-align:center\"><div><h1 style=\"font-size:72px;font-weight:900;line-height:1.15;margin:0\">90% of Landing Pages Make This Mistake</h1><p style=\"font-size:28px;opacity:0.6;margin-top:32px\">Swipe to find out →</p></div></div>"
}'
```

### Slides 2-6: Content Slides

One point per slide. Never cram multiple ideas.

```bash
# Content slide template
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1080px;height:1350px;background:#1e1b4b;padding:80px;font-family:system-ui;color:white;display:flex;flex-direction:column;justify-content:center\"><div><p style=\"font-size:120px;font-weight:900;color:#818cf8;margin:0;line-height:1\">01</p><h2 style=\"font-size:48px;margin:24px 0 16px;font-weight:800;line-height:1.2\">Your headline is too vague</h2><p style=\"font-size:26px;opacity:0.8;line-height:1.6\">\"Welcome to our platform\" tells the visitor nothing. Lead with the outcome: \"Ship docs in minutes, not days.\"</p></div></div>"
}'
```

### Slide 7: CTA Slide

```bash
# CTA slide
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1080px;height:1350px;background:linear-gradient(180deg,#312e81,#1e1b4b);display:flex;align-items:center;justify-content:center;padding:80px;font-family:system-ui;color:white;text-align:center\"><div><h2 style=\"font-size:56px;font-weight:900;margin:0;line-height:1.2\">Found this useful?</h2><p style=\"font-size:32px;opacity:0.8;margin-top:24px;line-height:1.5\">Save this post for later 🔖<br>Follow for more tips</p><p style=\"font-size:24px;opacity:0.4;margin-top:40px\">@yourusername</p></div></div>"
}'
```

## Design Rules

### Text Hierarchy

| Element | Size (at 1080px) | Weight |
|---------|-----------------|--------|
| Slide number | 96-120px | Black (900) |
| Heading | 48-64px | Bold (700-800) |
| Body text | 24-28px | Regular (400) |
| Caption/tag | 18-22px | Medium (500) |

### Readability

| Rule | Value |
|------|-------|
| Max words per slide | 30-40 |
| Max lines of body text | 4-5 |
| Line height | 1.5-1.6 |
| Font | Sans-serif (Inter, Montserrat, Poppins) |
| Text contrast | 4.5:1 minimum (WCAG AA) |

### Visual Consistency

| Element | Keep Consistent Across All Slides |
|---------|----------------------------------|
| Background color/gradient | Same palette, slight variations OK |
| Font family | Same font throughout |
| Text alignment | Same position (left or center) |
| Margins/padding | Same spacing |
| Accent color | Same highlight color |
| Numbering style | Same format (01, 02 or 1., 2.) |

## Carousel Types

### Educational / Tips

```
Slide 1: "5 CSS tricks you need to know"
Slide 2: Trick 1 with code example
Slide 3: Trick 2 with code example
...
Slide 6: Trick 5 with code example
Slide 7: "Follow for more dev tips"
```

### Storytelling / Case Study

```
Slide 1: "How we grew from 0 to $1M ARR"
Slide 2: The beginning (context)
Slide 3: The challenge
Slide 4: What we tried (failed)
Slide 5: What worked
Slide 6: The result (numbers)
Slide 7: Key takeaway + CTA
```

### Before / After

```
Slide 1: "I redesigned this landing page"
Slide 2: Before screenshot
Slide 3: Problem 1 annotated
Slide 4: After screenshot
Slide 5: Improvement 1 explained
Slide 6: Results (conversion lift)
Slide 7: "Want a review? DM me"
```

### Listicle / Tools

```
Slide 1: "10 tools every designer needs in 2025"
Slides 2-6: 2 tools per slide with logo + one-liner
Slide 7: "Save this for later 🔖"
```

## Swipe Psychology

| Principle | Application |
|-----------|------------|
| **Curiosity gap** | Hook promises value that requires swiping |
| **Numbered progress** | "3/7" creates completion drive |
| **Visual continuity** | Consistent design signals "there's more" |
| **Increasing value** | Best tip last — rewards completing |
| **Swipe cue** | Arrow or "Swipe →" on slide 1 |

## Batch Generation

```bash
# Generate all slides for a carousel
for i in 1 2 3 4 5 6 7; do
  infsh app run infsh/html-to-image --input "{
    \"html\": \"<div style='width:1080px;height:1350px;background:#1e1b4b;display:flex;align-items:center;justify-content:center;padding:80px;font-family:system-ui;color:white'><div style='text-align:center'><p style='font-size:28px;opacity:0.5'>Slide $i of 7</p></div></div>\"
  }" --no-wait
done
```

## AI-Generated Carousel Visuals

```bash
# Generate illustrations for each slide
infsh app run falai/flux-dev-lora --input '{
  "prompt": "minimal flat illustration, person at desk with laptop, clean modern style, simple shapes, limited color palette purple and blue tones, white background, icon style",
  "width": 1080,
  "height": 1080
}'
```

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Weak hook (slide 1) | Nobody swipes | Bold claim, question, or number + promise |
| Too much text per slide | Overwhelming, stops reading | Max 30-40 words per slide |
| No visual consistency | Looks like different posts | Same colors, fonts, margins throughout |
| No swipe indicator | People don't realize there's more | Add "Swipe →" or arrow on slide 1 |
| No CTA on last slide | Missed engagement opportunity | Ask to save, follow, share, or comment |
| Inconsistent numbering | Feels disorganized | Same number format on every content slide |
| Cramming 2+ ideas per slide | Hard to digest | One point per slide, always |
| Square format on Instagram | Wastes feed real estate | Use 1080x1350 (4:5) for more visibility |

## Related Skills

```bash
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@content-repurposing
npx skills add inference-sh/skills@linkedin-content
```

Browse all apps: `infsh app list`
