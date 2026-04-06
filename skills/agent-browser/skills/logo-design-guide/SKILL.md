---
name: logo-design-guide
description: "Logo design principles and AI image generation best practices for creating logos. Covers logo types, prompting techniques, scalability rules, and iteration workflows. Use for: brand identity, startup logos, app icons, favicons, logo concepts. Triggers: logo design, create logo, brand logo, logo generation, ai logo, logo maker, icon design, brand mark, logo concept, startup logo, app icon logo"
allowed-tools: Bash(infsh *)
---

# Logo Design Guide

Design effective logos with AI image generation via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a logo concept
infsh app run falai/flux-dev-lora --input '{
  "prompt": "flat vector logo of a mountain peak with a sunrise, minimal geometric style, single color, clean lines, white background",
  "width": 1024,
  "height": 1024
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Logo Types

| Type | Description | When to Use | Example |
|------|-------------|-------------|---------|
| **Wordmark** | Company name styled as logo | Strong brand name, short (< 10 chars) | Google, Coca-Cola |
| **Lettermark** | Initials only | Long company name, formal | IBM, HBO, CNN |
| **Pictorial** | Recognizable icon/symbol | Universal brand, works without text | Apple, Twitter bird |
| **Abstract** | Geometric/non-literal shape | Tech companies, conceptual brands | Nike swoosh, Pepsi |
| **Mascot** | Character illustration | Friendly brands, food/sports | KFC Colonel, Pringles |
| **Combination** | Icon + wordmark together | New brands needing both recognition and name | Burger King, Adidas |

## Critical AI Limitation

**AI image generators cannot reliably render text.** Letters will be distorted, misspelled, or garbled.

Strategy:
1. Generate the **icon/symbol only** with AI
2. Add text/wordmark in a design tool (Figma, Canva, Illustrator)
3. Or use a combination approach: AI icon + manually set typography

## Prompting for Logos

### Keywords That Work

```
flat vector logo, simple minimal icon, single color silhouette,
geometric logo mark, clean lines, negative space design,
line art logo, flat design icon, minimalist symbol
```

### Keywords That Fail

```
❌ photorealistic logo (contradiction — logos aren't photos)
❌ 3D rendered logo (too complex, won't scale down)
❌ gradient logo (inconsistent results, hard to reproduce)
❌ logo with text "Company Name" (text rendering fails)
```

### Prompt Structure

```
flat vector logo of [subject], [style], [color constraint], [background], [additional detail]
```

### Examples by Logo Type

```bash
# Abstract geometric
infsh app run falai/flux-dev-lora --input '{
  "prompt": "flat vector abstract logo, interlocking hexagonal shapes forming a letter S, minimal geometric style, single navy blue color, white background, clean sharp edges"
}'

# Pictorial nature
infsh app run falai/flux-dev-lora --input '{
  "prompt": "flat vector logo of a fox head in profile, geometric faceted style, orange and white, minimal clean lines, white background, negative space design"
}'

# Mascot style
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "friendly cartoon owl mascot logo, simple flat illustration, wearing graduation cap, purple and gold colors, white background, clean vector style"
}'

# Tech abstract
infsh app run xai/grok-imagine-image-pro --input '{
  "prompt": "minimal abstract logo mark, interconnected nodes forming a brain shape, line art style, single teal color, white background, tech startup aesthetic"
}'
```

## Scalability Rules

A logo must work at every size:

| Context | Size | What Must Work |
|---------|------|----------------|
| Favicon | 16x16 px | Silhouette recognizable |
| App icon | 1024x1024 px | Full detail visible |
| Social avatar | 400x400 px | Clear at a glance |
| Business card | ~1 inch | Clean print reproduction |
| Billboard | 10+ feet | No pixelation, simple enough |

### Scalability Checklist

- [ ] Recognizable as a 16px favicon (squint test)
- [ ] Works in single color (black on white)
- [ ] Works inverted (white on black)
- [ ] No tiny details that disappear at small sizes
- [ ] No thin lines that vanish when shrunk
- [ ] Clear silhouette without color

## Color Guidelines

- **Maximum 2-3 colors** for the primary logo
- Must work in **single color** (black, white, or brand primary)
- Consider **color psychology**:
  - Blue: trust, professional (finance, tech, healthcare)
  - Red: energy, urgency (food, entertainment, retail)
  - Green: growth, nature (health, sustainability, finance)
  - Orange: friendly, creative (startups, youth brands)
  - Purple: luxury, wisdom (beauty, education)
  - Black: premium, elegant (fashion, luxury, tech)
- Test on both light and dark backgrounds

## Iteration Workflow

```bash
# Step 1: Generate 5-10 broad concepts
for i in {1..5}; do
  infsh app run falai/flux-dev-lora --input '{
    "prompt": "flat vector logo of a lighthouse, minimal geometric, single color, white background"
  }' --no-wait
done

# Step 2: Refine the best concept with variations
infsh app run falai/flux-dev-lora --input '{
  "prompt": "flat vector logo of a geometric lighthouse with light beam rays, minimal line art, navy blue, white background, negative space design"
}'

# Step 3: Generate at high resolution for final
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "flat vector logo of a geometric lighthouse with radiating light beams, minimal clean design, navy blue single color, pure white background",
  "size": "2K"
}'

# Step 4: Upscale for production use
infsh app run falai/topaz-image-upscaler --input '{
  "image": "path/to/best-logo.png",
  "scale": 4
}'
```

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Too much detail | Loses clarity at small sizes | Simplify to essential shapes |
| Relies on color | Fails in B&W contexts | Design in black first |
| Text in AI generation | Garbled/misspelled letters | Generate icon only, add text manually |
| Trendy effects (glows, shadows) | Dates quickly, reproduction issues | Stick to flat, timeless design |
| Too many colors | Hard to reproduce, expensive printing | Max 2-3 colors |
| Asymmetric without purpose | Looks unfinished | Use intentional asymmetry or stay balanced |

## File Format Delivery

| Format | Use Case |
|--------|----------|
| SVG | Scalable vector, web, editing |
| PNG (transparent) | Digital use, presentations |
| PNG (white bg) | Documents, email signatures |
| ICO / Favicon | Website favicon (16, 32, 48px) |
| High-res PNG (4096px+) | Print, billboards |

Note: AI generates raster images (PNG). For true vector SVG, use the AI output as a reference and trace in a vector tool, or use AI-to-SVG conversion tools.

## Related Skills

```bash
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
