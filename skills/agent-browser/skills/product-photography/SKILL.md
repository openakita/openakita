---
name: product-photography
description: "AI product photography with studio lighting, lifestyle shots, and packshot conventions. Covers angles, backgrounds, shadow types, hero shots, and e-commerce image requirements. Use for: product photos, e-commerce images, Amazon listings, packshots, lifestyle photography. Triggers: product photography, product photo, packshot, e-commerce photography, product shot, product image, studio photography, lifestyle product, amazon product photo, product listing image, hero shot, product mockup, commercial photography"
allowed-tools: Bash(infsh *)
---

# Product Photography

Create professional product images with AI via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Clean studio packshot
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "professional product photography, single premium wireless headphone on clean white background, soft studio lighting with subtle shadow, commercial e-commerce style, sharp focus, 4K quality",
  "size": "2K"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Shot Types

### 1. Hero Shot (Primary Image)

The main image customers see first. Clean, focused, aspirational.

```bash
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "hero product shot, premium smartwatch floating at slight angle, clean gradient background transitioning from white to light grey, dramatic rim lighting, subtle reflection below, commercial photography, magazine quality, sharp details",
  "size": "2K"
}'
```

| Rule | Why |
|------|-----|
| Product fills 80% of frame | Maximizes visual impact |
| Slight angle (15-30 degrees) | Adds dimension vs flat front-on |
| One hero light + fill | Creates depth without harsh shadows |
| Neutral or brand-color background | Keeps focus on product |

### 2. Packshot (E-Commerce White Background)

Amazon, Shopify, and most marketplaces require pure white backgrounds.

```bash
# Pure white background packshot
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "product packshot, leather wallet standing upright at slight angle on pure white background #FFFFFF, soft even studio lighting, no shadows, e-commerce product photography, Amazon listing style, clean sharp focus",
  "size": "2K"
}'
```

**Amazon Requirements:**
- Pure white background (RGB 255, 255, 255)
- Product fills 85%+ of frame
- No props, text, logos, or watermarks
- Minimum 1000px on longest side (1600px+ recommended for zoom)
- JPEG or PNG, sRGB color

### 3. Lifestyle Shot

Product in context — shows how it's used or where it belongs.

```bash
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "lifestyle product photography, premium coffee mug on rustic wooden table beside an open book and reading glasses, morning sunlight streaming through window, cozy home atmosphere, shallow depth of field, warm tones, editorial style",
  "size": "2K"
}'
```

### 4. Scale Shot

Shows product size relative to familiar objects or human hands.

```bash
infsh app run falai/flux-dev-lora --input '{
  "prompt": "product scale photography, compact portable speaker held in one hand, person showing how small and portable it is, clean blurred background, natural lighting, lifestyle tech photography",
  "width": 1024,
  "height": 1024
}'
```

### 5. Detail / Close-Up Shot

Highlights texture, material quality, or specific features.

```bash
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "extreme close-up product detail, premium leather bag stitching and grain texture, macro photography, shallow depth of field, soft directional lighting highlighting texture, luxury product photography, editorial quality",
  "size": "2K"
}'
```

### 6. Group / Collection Shot

Multiple products or variants together.

```bash
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "product collection flat lay photography, three skincare bottles arranged in triangular composition on marble surface, minimal props, soft overhead lighting, beauty product photography, editorial style, coordinated brand aesthetic",
  "size": "2K"
}'
```

## Camera Angles

| Angle | Best For | Prompt Keyword |
|-------|----------|---------------|
| **Eye level** | Most products, relatable | "eye level shot", "straight on" |
| **Slight above (30°)** | Flat lay, food, cosmetics | "overhead angle", "45 degree angle" |
| **Bird's eye (90°)** | Flat lay compositions | "flat lay", "top down", "overhead" |
| **Low angle** | Making products look powerful/premium | "low angle", "looking up at product" |
| **3/4 angle** | Most versatile, shows depth | "three-quarter view", "slight angle" |

## Lighting Setups

| Setup | Look | Prompt Keywords |
|-------|------|----------------|
| **Soft box (diffused)** | Even, minimal shadows | "soft studio lighting", "diffused light" |
| **Rim/edge lighting** | Dramatic outline glow | "rim lighting", "edge light", "backlit" |
| **Natural window** | Warm, authentic, lifestyle | "natural window light", "golden hour" |
| **Hard directional** | Strong shadows, editorial | "dramatic directional lighting", "hard shadow" |
| **Flat/even** | E-commerce, no shadows | "even lighting", "no shadows", "flat light" |

## Shadow Types

| Shadow | Effect | When to Use |
|--------|--------|-------------|
| **No shadow** | Clean, floating | Amazon/e-commerce requirements |
| **Contact shadow** | Tiny shadow where product meets surface | Grounded but clean |
| **Drop shadow** | Soft shadow below product | Adds depth, professional |
| **Dramatic shadow** | Long, directional shadow | Editorial, luxury, mood |
| **Reflection** | Mirror-like surface below | Tech, luxury, premium feel |

## Background Guide

| Background | Best For | Prompt Keywords |
|------------|----------|----------------|
| Pure white (#FFFFFF) | E-commerce, marketplaces | "pure white background" |
| Light grey gradient | Hero shots, premium | "gradient background white to grey" |
| Marble/stone | Luxury, beauty, jewelry | "marble surface" |
| Wood/rustic | Artisan, food, natural products | "rustic wooden table" |
| Colored (brand) | Brand consistency | "background color [hex]" |
| Lifestyle environment | Context shots | "kitchen counter", "desk", "bathroom shelf" |

## Composition Rules

| Rule | Application |
|------|------------|
| **Rule of thirds** | Place product at intersection points for lifestyle shots |
| **Center dominant** | E-commerce/packshots — product dead center |
| **Negative space** | Leave room for text overlay if marketing use |
| **Leading lines** | Use table edges, shadows to draw eye to product |
| **Odd numbers** | Groups of 3 or 5 products look better than 2 or 4 |
| **Triangle composition** | Arrange 3 items in a triangle for balance |

## E-Commerce Image Set

A complete product listing needs 7-9 images in this order:

| Position | Image Type | Purpose |
|----------|-----------|---------|
| 1 | **Hero / packshot** | Primary listing image, white background |
| 2 | **Lifestyle** | Product in use/context |
| 3 | **Feature callout** | Key feature highlighted |
| 4 | **Scale reference** | Size in hand or next to known object |
| 5 | **Detail close-up** | Material quality, craftsmanship |
| 6 | **Alternate angle** | Back or side view |
| 7 | **Infographic** | Dimensions, specs, what's included |
| 8 | **Packaging** | Unboxing experience |
| 9 | **Social proof** | Rating overlay or lifestyle with caption |

```bash
# Generate a complete e-commerce set
# 1. Hero packshot
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "product packshot, premium bluetooth speaker on pure white background, slight angle, soft studio lighting, subtle contact shadow, e-commerce photography, sharp, 4K",
  "size": "2K"
}' --no-wait

# 2. Lifestyle
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "lifestyle product photography, bluetooth speaker on poolside table, summer setting, sunglasses and drink nearby, warm natural light, vacation vibes, editorial style",
  "size": "2K"
}' --no-wait

# 3. Detail
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "extreme close-up of speaker grille texture and premium materials, macro product photography, soft lighting, showing build quality, sharp detail",
  "size": "2K"
}' --no-wait

# 4. Scale
infsh app run falai/flux-dev-lora --input '{
  "prompt": "person holding compact bluetooth speaker in one hand, showing portable size, clean blurred background, natural light, lifestyle tech photography",
  "width": 1024,
  "height": 1024
}' --no-wait
```

## Product Categories

### Food & Beverage

```
Key: overhead angles, natural light, visible texture, steam/freshness cues
Prompt add: "food photography, appetizing, fresh, natural daylight, shallow depth of field"
Avoid: artificial-looking colors, perfectly symmetrical plating (looks fake)
```

### Jewelry & Accessories

```
Key: macro detail, reflective surfaces, black or gradient backgrounds
Prompt add: "jewelry photography, macro, sparkle, reflective surface, luxury"
Avoid: flat lighting (kills sparkle), busy backgrounds
```

### Electronics & Tech

```
Key: clean lines, dark or gradient backgrounds, rim lighting
Prompt add: "tech product photography, sleek, modern, rim lighting, premium"
Avoid: warm/rustic backgrounds (wrong aesthetic)
```

### Cosmetics & Beauty

```
Key: flat lay or slight angle, marble/clean surfaces, soft pastels
Prompt add: "beauty product photography, clean, minimal, soft light, editorial"
Avoid: harsh shadows, dark moody lighting (unless luxury/niche)
```

### Apparel & Fashion

```
Key: on model or flat lay, lifestyle context, brand mood
Prompt add: "fashion photography, editorial, styled, natural pose"
Avoid: pure white background for lifestyle (save for e-commerce only)
```

## Image Editing Workflow

```bash
# Generate base product image
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "premium headphones on white background, studio product photography",
  "size": "2K"
}'

# Edit: change background to lifestyle
infsh app run bytedance/seededit-3-0-i2i --input '{
  "prompt": "change the background to a modern minimalist desk setup with warm afternoon light, keep the headphones exactly the same",
  "image": "headphones-white.png"
}'

# Upscale for print
infsh app run falai/topaz-image-upscaler --input '{
  "image": "headphones-lifestyle.png"
}'
```

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Busy backgrounds | Product gets lost | Clean, simple backgrounds — product is the star |
| Flat front-on angle | Looks like a mugshot | Slight 15-30° angle adds dimension |
| Wrong lighting for category | Tech on rustic wood, food in cold light | Match lighting to product category conventions |
| Too many props | Distracts from product | Max 2-3 supporting props for lifestyle shots |
| Inconsistent style across set | Looks unprofessional | Same lighting setup, same background family |
| No scale reference | Customers can't judge size | Include at least one shot with hands or known objects |
| Low resolution | Can't zoom, looks amateur | Generate at 2K+ and upscale if needed |
| Perfectly centered everything | Static, boring layout | Use rule of thirds for lifestyle, center only for packshots |

## Related Skills

```bash
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@flux-image
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
