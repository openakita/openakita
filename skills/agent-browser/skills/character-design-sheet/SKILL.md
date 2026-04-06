---
name: character-design-sheet
description: "Character consistency across AI-generated images with reference sheets and LoRA techniques. Covers turnaround views, expression sheets, color palettes, and style consistency tricks. Use for: character design, game art, illustration, animation, comics, visual novels. Triggers: character design, character sheet, character consistency, character reference, turnaround sheet, expression sheet, character art, consistent character, character concept, reference sheet, character creation, oc design, character bible"
allowed-tools: Bash(infsh *)
---

# Character Design Sheet

Create consistent characters across multiple AI-generated images via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a character concept
infsh app run falai/flux-dev-lora --input '{
  "prompt": "character design reference sheet, front view of a young woman with short red hair, green eyes, wearing a blue jacket and white t-shirt, full body, white background, clean lines, concept art style, character turnaround",
  "width": 1024,
  "height": 1024
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## The Consistency Problem

AI image generation produces different-looking characters every time, even with the same prompt. This is the #1 challenge in AI art for any project requiring the same character across multiple images.

### Solutions (Ranked by Effectiveness)

| Technique | Consistency | Effort | Best For |
|-----------|-------------|--------|----------|
| **FLUX LoRA** (trained on character) | Very high | High (requires training data) | Ongoing projects, many images |
| **Detailed description anchor** | Medium-high | Low | Quick projects, few images |
| **Same seed + similar prompt** | Medium | Low | Variations of single pose |
| **Image-to-image refinement** | Medium | Medium | Refining existing images |
| **Reference image in prompt** | Varies | Low | When model supports it |

## Reference Sheet Types

### 1. Turnaround Sheet

Shows the character from multiple angles:

```
┌────────┬────────┬────────┬────────┐
│        │        │        │        │
│ FRONT  │  3/4   │  SIDE  │  BACK  │
│  VIEW  │  VIEW  │  VIEW  │  VIEW  │
│        │        │        │        │
└────────┴────────┴────────┴────────┘
```

```bash
# Generate front view
infsh app run falai/flux-dev-lora --input '{
  "prompt": "character design, front view, young woman with short asymmetric red hair, bright green eyes, wearing navy blue bomber jacket over white graphic tee, dark jeans, red sneakers, standing in neutral pose, full body, clean white background, concept art, sharp details",
  "width": 768,
  "height": 1024
}' --no-wait

# Generate 3/4 view (same description)
infsh app run falai/flux-dev-lora --input '{
  "prompt": "character design, three-quarter view, young woman with short asymmetric red hair, bright green eyes, wearing navy blue bomber jacket over white graphic tee, dark jeans, red sneakers, standing, full body, clean white background, concept art, sharp details",
  "width": 768,
  "height": 1024
}' --no-wait

# Generate side view
infsh app run falai/flux-dev-lora --input '{
  "prompt": "character design, side profile view, young woman with short asymmetric red hair, bright green eyes, wearing navy blue bomber jacket over white graphic tee, dark jeans, red sneakers, standing, full body, clean white background, concept art, sharp details",
  "width": 768,
  "height": 1024
}' --no-wait

# Generate back view
infsh app run falai/flux-dev-lora --input '{
  "prompt": "character design, back view, young woman with short asymmetric red hair, wearing navy blue bomber jacket over white graphic tee, dark jeans, red sneakers, standing, full body, clean white background, concept art, sharp details",
  "width": 768,
  "height": 1024
}' --no-wait

# Stitch into reference sheet
infsh app run infsh/stitch-images --input '{
  "images": ["front.png", "three-quarter.png", "side.png", "back.png"],
  "direction": "horizontal"
}'
```

### 2. Expression Sheet

Shows the character's face with different emotions:

```
┌────────┬────────┬────────┐
│NEUTRAL │ HAPPY  │ ANGRY  │
│        │        │        │
├────────┼────────┼────────┤
│  SAD   │SURPRISE│THINKING│
│        │        │        │
└────────┴────────┴────────┘
```

Minimum 6 expressions: neutral, happy, angry, sad, surprised, thinking.

```bash
# Neutral
infsh app run falai/flux-dev-lora --input '{
  "prompt": "character portrait, close-up face, young woman with short red hair and green eyes, neutral calm expression, clean white background, concept art, consistent character design",
  "width": 512,
  "height": 512
}' --no-wait

# Happy
infsh app run falai/flux-dev-lora --input '{
  "prompt": "character portrait, close-up face, young woman with short red hair and green eyes, warm genuine smile, happy expression, clean white background, concept art, consistent character design",
  "width": 512,
  "height": 512
}' --no-wait

# Angry
infsh app run falai/flux-dev-lora --input '{
  "prompt": "character portrait, close-up face, young woman with short red hair and green eyes, furrowed brows, angry determined expression, clean white background, concept art, consistent character design",
  "width": 512,
  "height": 512
}' --no-wait

# (Continue for sad, surprised, thinking...)
```

### 3. Outfit/Costume Sheet

Multiple outfits for the same character:

| Outfit | Description |
|--------|-------------|
| Casual | Bomber jacket, t-shirt, jeans |
| Work | Blazer, button-down, slacks |
| Athletic | Sports bra, leggings, running shoes |
| Formal | Evening dress, heels |

### 4. Color Palette Sheet

Document exact colors for consistency:

```
CHARACTER: Maya Chen

Skin:    ████ #F5D0A9 (warm beige)
Hair:    ████ #C0392B (auburn red)
Eyes:    ████ #27AE60 (emerald green)
Jacket:  ████ #2C3E50 (navy blue)
T-shirt: ████ #ECF0F1 (off-white)
Jeans:   ████ #34495E (dark slate)
Shoes:   ████ #E74C3C (bright red)
```

## The Description Anchor Technique

The most practical consistency technique: write a **50+ word detailed description** and reuse it exactly in every prompt.

### Template

```
[age] [gender] with [hair: color, length, style], [eye color] eyes,
[skin tone], [facial features: any distinctive marks],
wearing [top: specific color and style], [bottom: specific color and style],
[shoes: specific color and style], [accessories: specific items]
```

### Example

```
young woman in her mid-twenties with short asymmetric auburn red hair
swept to the right side, bright emerald green eyes, light warm skin
with a small beauty mark below her left eye, wearing a fitted navy
blue bomber jacket with silver zipper over a white crew-neck t-shirt,
dark slate slim jeans, and bright red canvas sneakers, small silver
stud earrings
```

**Use this exact block in EVERY prompt** for this character, only changing the action/pose/scene.

## Proportion Guide

| Style | Head-to-Body Ratio | Best For |
|-------|-------------------|----------|
| Realistic | 7.5 : 1 | Film, photorealistic |
| Heroic | 8 : 1 | Superheroes, action |
| Anime/Manga | 5-6 : 1 | Japanese animation style |
| Stylized | 4-5 : 1 | Western animation |
| Chibi/Super-deformed | 2-3 : 1 | Cute, comedic, mascots |

Include proportion style in your prompts: "realistic proportions" vs "anime style proportions" vs "chibi proportions"

## Using LoRA for Consistency

For projects requiring many images of the same character, train a LoRA:

```bash
# Use FLUX with a character LoRA
infsh app run falai/flux-dev-lora --input '{
  "prompt": "maya_chen character, sitting at a cafe reading a book, warm afternoon light, candid photography style",
  "loras": [{"path": "path/to/maya-chen-lora.safetensors", "scale": 0.8}]
}'
```

**LoRA Training Tips:**
- Need 10-20 reference images of the character (consistent style)
- Train on specific trigger word (e.g., "maya_chen")
- Scale 0.7-0.9 balances consistency with prompt flexibility
- Lower scale = more creative freedom, higher = more strict matching

## Common Consistency Failures

| Issue | Why It Happens | Mitigation |
|-------|---------------|------------|
| **Hair color drift** | Model interprets "red hair" differently each time | Use specific shade: "auburn red #C0392B" |
| **Eye color change** | Low priority in generation | Mention eye color early in prompt |
| **Outfit inconsistency** | Model fills in details creatively | Describe every clothing item explicitly |
| **Age shift** | Vague age description | Use "mid-twenties" not "young" |
| **Face structure change** | Different generations = different faces | Use LoRA or same seed base |
| **Proportion shift** | Style interpretation varies | Specify "7.5 head proportions" |

## Character Bible Template

For ongoing projects, maintain a character bible document:

```markdown
# Character: Maya Chen

## Visual Description (use in all prompts)
young woman in her mid-twenties with short asymmetric auburn red hair...
[full 50+ word anchor description]

## Color Palette
- Skin: #F5D0A9
- Hair: #C0392B
- Eyes: #27AE60
- Primary outfit: Navy #2C3E50
- Accent: Red #E74C3C

## Personality Notes (for expression/pose choices)
- Confident but approachable
- Default expression: slight curious smile
- Gestures: talks with hands, leans forward when interested

## Style Keywords
concept art, clean lines, sharp details, [art style reference]

## LoRA (if trained)
Path: ./loras/maya-chen-v2.safetensors
Trigger: maya_chen
Recommended scale: 0.8
```

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Vague descriptions | Different character every time | 50+ word detailed anchor |
| Inconsistent prompt structure | Varying emphasis = varying results | Same structure, only change action/scene |
| Generating one view only | Can't use character in different contexts | Create full turnaround reference |
| No color documentation | Colors drift across generations | Record exact hex codes |
| Skipping expression sheet | Character feels one-dimensional | Generate 6+ expressions |
| Not using LoRA for big projects | Inconsistency compounds | Train LoRA for 10+ image projects |

## Related Skills

```bash
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@flux-image
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
