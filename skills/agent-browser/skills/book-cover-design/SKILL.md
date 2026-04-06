---
name: book-cover-design
description: "Book cover design with genre-specific conventions, typography rules, and AI image generation. Covers fiction and non-fiction genres, sizing, thumbnail testing, and iteration workflows. Use for: self-publishing, ebook covers, print covers, audiobook covers, cover mockups. Triggers: book cover, cover design, ebook cover, book art, novel cover, self publishing cover, kindle cover, audiobook cover, book jacket, cover illustration, fiction cover, nonfiction cover"
allowed-tools: Bash(infsh *)
---

# Book Cover Design

Create genre-appropriate book covers with AI image generation via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a thriller cover concept
infsh app run falai/flux-dev-lora --input '{
  "prompt": "dark moody book cover art, lone figure standing at end of a rain-soaked alley, dramatic chiaroscuro lighting, noir atmosphere, cinematic, high contrast shadows",
  "width": 832,
  "height": 1248
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Genre Conventions

Readers judge books by covers. Matching genre expectations is critical — a romance reader will skip a cover that looks like sci-fi, regardless of content.

### Fiction

| Genre | Palette | Imagery | Typography | Mood |
|-------|---------|---------|------------|------|
| **Thriller/Mystery** | Dark (black, navy, blood red) | Lone figure, urban scenes, shadows | Bold sans-serif, all caps | Tense, ominous |
| **Romance** | Warm (pink, red, gold, soft purple) | Couples, flowers, scenic backdrops | Script/cursive, elegant serif | Passionate, dreamy |
| **Sci-Fi** | Cool (blue, teal, purple, silver) | Space, tech, geometric shapes | Clean sans-serif, futuristic | Vast, technological |
| **Fantasy** | Rich saturated (emerald, crimson, gold) | Swords, magic, landscapes, creatures | Decorative serif, ornamental | Epic, magical |
| **Literary Fiction** | Muted, sophisticated | Abstract, minimal, symbolic | Elegant serif, understated | Thoughtful, artistic |
| **Horror** | Dark with high contrast pops | Faces, shadows, isolation, decay | Distressed, bold, dripping | Dread, unease |
| **Historical** | Sepia, muted period-accurate | Period clothing, architecture, artifacts | Classical serif | Nostalgic, authentic |

### Non-Fiction

| Category | Style | Imagery | Typography |
|----------|-------|---------|------------|
| **Business/Self-help** | Clean, bold, 2-3 colors | Minimal or none, icon optional | Large bold sans-serif title |
| **Memoir** | Personal, warm | Author photo or atmospheric scene | Mix of serif and sans |
| **Science/Academic** | Professional, structured | Diagrams, abstract visuals | Clean serif, structured layout |
| **Cookbook** | Appetizing, bright | Hero food photograph | Warm, inviting fonts |
| **Travel** | Vibrant, aspirational | Destination photography | Adventure-style fonts |

## Cover Sizing

### Print (Trim Sizes)

| Format | Dimensions | Common For |
|--------|-----------|------------|
| Mass market paperback | 4.25 x 6.87" | Genre fiction |
| Trade paperback | 5.5 x 8.5" | Most fiction/non-fiction |
| Standard | 6 x 9" | Non-fiction, textbooks |
| Large format | 7 x 10" | Coffee table, art books |

### Digital

| Platform | Cover Size | Aspect Ratio |
|----------|-----------|--------------|
| Amazon Kindle | 2560 x 1600 px (min 1000 x 625) | 1.6:1 |
| Apple Books | 1400 x 1873 px minimum | ~3:4 |
| General ebook | 2500 x 3750 px | 2:3 |

### Spine Width

Approximate: **page count / 400 = spine width in inches** (varies by paper stock).
- 200 pages ≈ 0.5" spine
- 400 pages ≈ 1.0" spine

## Layout Zones

```
┌─────────────────────────┐
│     TITLE ZONE          │ ← Top 1/3: Title must be readable here
│     (largest text)      │    This is what shows in thumbnails
│                         │
│                         │
│     MAIN IMAGE          │ ← Middle: Core visual/illustration
│     ZONE                │    The emotional hook
│                         │
│                         │
│     SUBTITLE /          │ ← Bottom area: Author name, subtitle
│     AUTHOR ZONE         │    Smaller text, less critical at thumbnail
└─────────────────────────┘
```

## The Thumbnail Test

Your cover will be seen at **80x120px** on Amazon, 60x90px in search results, and ~40x60px on mobile grids.

**At thumbnail size, readers must be able to identify:**
1. The genre (from color and composition)
2. The title (if large enough)
3. The mood (from imagery)

**Test:** shrink your cover to 80px wide. If you can't read the title or identify the genre, redesign.

## Prompting by Genre

### Thriller
```bash
infsh app run falai/flux-dev-lora --input '{
  "prompt": "dark cinematic book cover scene, silhouette of a person standing before a foggy bridge at night, single streetlamp casting long shadows, noir atmosphere, high contrast, desaturated blue tint, dramatic tension",
  "width": 832,
  "height": 1248
}'
```

### Romance
```bash
infsh app run bytedance/seedream-4-5 --input '{
  "prompt": "romantic soft-focus scene, couple silhouetted against golden sunset on a beach, warm pink and gold tones, bokeh lights, dreamy atmosphere, soft pastel sky, intimate mood",
  "size": "2K"
}'
```

### Sci-Fi
```bash
infsh app run falai/flux-dev-lora --input '{
  "prompt": "science fiction book cover art, massive space station orbiting a ringed planet, deep blue and teal color palette, stars and nebula background, hard sci-fi aesthetic, cinematic scale, clean geometric architecture",
  "width": 832,
  "height": 1248
}'
```

### Fantasy
```bash
infsh app run xai/grok-imagine-image-pro --input '{
  "prompt": "epic fantasy book cover illustration, ancient stone castle on a cliff overlooking a misty valley, magical aurora in the sky, rich emerald and gold colors, detailed environment, sense of wonder and adventure",
  "aspect_ratio": "2:3"
}'
```

### Non-Fiction / Business
```bash
infsh app run falai/flux-dev-lora --input '{
  "prompt": "minimal abstract book cover background, clean gradient from deep navy to white, subtle geometric pattern, professional and modern, negative space, corporate aesthetic",
  "width": 832,
  "height": 1248
}'
```

## Typography Rules

**AI cannot render text reliably.** Generate the cover art/background with AI, then add typography in a design tool.

### Title Hierarchy
1. **Title** — largest, most prominent, top 1/3 of cover
2. **Subtitle** — smaller, below title or at bottom
3. **Author name** — bottom of cover, size depends on author recognition

### Font Pairing by Genre
- Thriller: bold sans-serif title + condensed sans-serif author
- Romance: script/cursive title + elegant serif author
- Sci-Fi: geometric sans-serif for both
- Fantasy: decorative/medieval serif title + clean serif author
- Business: heavy bold sans-serif title + light sans-serif subtitle

## Iteration Workflow

```bash
# 1. Generate 5+ concepts across different models
infsh app run falai/flux-dev-lora --input '{"prompt": "...", "width": 832, "height": 1248}' --no-wait
infsh app run bytedance/seedream-4-5 --input '{"prompt": "..."}' --no-wait
infsh app run xai/grok-imagine-image-pro --input '{"prompt": "...", "aspect_ratio": "2:3"}' --no-wait

# 2. Refine best concept with image-to-image editing
infsh app run bytedance/seededit-3-0-i2i --input '{
  "prompt": "make the sky more dramatic with storm clouds, increase contrast",
  "image": "path/to/best-concept.png"
}'

# 3. Upscale for print quality
infsh app run falai/topaz-image-upscaler --input '{
  "image": "path/to/final-cover.png",
  "scale": 4
}'
```

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Wrong genre signals | Readers skip it | Study bestsellers in your genre |
| Title too small | Invisible at thumbnail | Title should fill top 1/3 |
| Too much detail | Muddy at small sizes | Simplify, use negative space |
| AI-generated text | Garbled letters | Add text in design tool |
| Centered everything | Static, boring | Use asymmetry intentionally |
| Following trends blindly | Dates quickly | Classic genre conventions endure |

## Checklist

- [ ] Genre instantly recognizable from colors and composition
- [ ] Title readable at 80px wide (thumbnail test)
- [ ] No AI-generated text on cover
- [ ] Works in both color and greyscale
- [ ] Correct dimensions for target platform
- [ ] Author name visible but not competing with title
- [ ] High resolution (300 DPI for print, 2500px+ for digital)
- [ ] Spine text readable (for print)

## Related Skills

```bash
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@prompt-engineering
npx skills add inference-sh/skills@image-upscaling
```

Browse all apps: `infsh app list`
