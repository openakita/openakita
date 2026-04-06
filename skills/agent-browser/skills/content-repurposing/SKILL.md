---
name: content-repurposing
description: "Content atomization — turn one piece of content into many formats. Covers blog-to-thread, blog-to-carousel, podcast-to-blog, video-to-quotes, and more. Use for: content marketing, social media, multi-platform distribution, content strategy. Triggers: content repurposing, repurpose content, content atomization, content recycling, one to many content, multi platform content, cross post, adapt content, reformat content, blog to thread, blog to video, podcast to blog, content multiplication"
allowed-tools: Bash(infsh *)
---

# Content Repurposing

Turn one piece of content into many formats via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a quote card from a blog pull-quote
infsh app run falai/flux-dev-lora --input '{
  "prompt": "minimal quote card design, dark navy background, large white quotation marks, clean sans-serif typography space, modern professional design, social media post format",
  "width": 1024,
  "height": 1024
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## The Content Pyramid

One source piece can generate 10+ derivative assets:

```
            ┌──────────┐
            │ LONG-FORM │  Blog post, podcast, video, whitepaper
            │  SOURCE   │
            └─────┬─────┘
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
   ┌─────────┐ ┌──────┐ ┌──────────┐
   │ MEDIUM  │ │MEDIUM│ │  MEDIUM  │  Newsletter, LinkedIn, email
   │ FORMAT  │ │FORMAT│ │  FORMAT  │
   └────┬────┘ └──┬───┘ └────┬─────┘
        │         │          │
   ┌────┼────┐    │     ┌────┼────┐
   ▼    ▼    ▼    ▼     ▼    ▼    ▼
 ┌───┐┌───┐┌───┐┌───┐┌───┐┌───┐┌───┐  Tweets, quotes, audiograms,
 │   ││   ││   ││   ││   ││   ││   │  short clips, infographic tiles
 └───┘└───┘└───┘└───┘└───┘└───┘└───┘
```

## Conversion Recipes

### Blog Post -> Twitter/X Thread

**Extract 5-8 key insights. One per tweet. Add hook.**

| Element | Rule |
|---------|------|
| Hook tweet | Listicle, contrarian, or promise format |
| Body tweets | One insight per tweet, 280 chars max |
| Visual breaks | Add image every 3-4 tweets |
| Final tweet | CTA + "RT the first tweet if useful" |

**Adaptation:**
- Remove nuance and caveats (threads are punchy)
- Add numbers and specifics (threads need skimmability)
- Cut academic language (threads are conversational)

```bash
# Generate a visual for the thread
infsh app run falai/flux-dev-lora --input '{
  "prompt": "clean infographic tile, single statistic 60% highlighted in large bold text, minimal dark background, data visualization style, professional",
  "width": 1024,
  "height": 1024
}'

# Post the thread
infsh app run x/post-create --input '{
  "text": "I analyzed 500 landing pages.\n\nHere are 7 patterns the top converters all share:\n\n🧵 Thread:"
}'
```

### Blog Post -> LinkedIn Carousel

**1 slide per section. 8-12 slides total.**

| Slide | Content |
|-------|---------|
| 1 (Hook) | Bold claim or question from headline |
| 2-9 (Content) | One key point per slide, large text, supporting visual |
| 10 (Summary) | Recap the key takeaways |
| 11 (CTA) | "Follow for more" / "Save this" / "Comment your thoughts" |

**Specs:** 1080x1080 (square) or 1080x1350 (4:5 for more space)

```bash
# Generate carousel slides
for i in {1..10}; do
  infsh app run falai/flux-dev-lora --input "{
    \"prompt\": \"clean minimal presentation slide, dark gradient background, large text area, professional business design, slide $i of 10, consistent style\",
    \"width\": 1024,
    \"height\": 1024
  }" --no-wait
done
```

### Blog Post -> Newsletter Section

**3-line summary + "why it matters" + link.**

```
## This Week's Feature: [Title]

[1-2 sentence summary of the key insight]

**Why it matters:** [1 sentence connecting to reader's work/life]

→ [Read the full post](link)
```

### Blog Post -> Short-Form Video Script

**Problem + key insight + CTA. Under 60 seconds.**

| Section | Duration | Content |
|---------|----------|---------|
| Hook | 3s | "Most people get [topic] wrong." |
| Problem | 10s | State the common mistake |
| Insight | 25s | Your key finding/advice |
| Proof | 10s | One stat or example |
| CTA | 5s | "Follow for more" / "Link in bio" |

```bash
# Generate voiceover
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] Most landing pages make this mistake. They put the features above the fold instead of the outcome. Top converting pages show what life looks like AFTER using the product. Try it and watch your conversion rate climb."
}'

# Generate video
infsh app run google/veo-3-1-fast --input '{
  "prompt": "Screen recording style, scrolling through a well-designed landing page, clean modern UI, smooth scroll, professional website"
}'
```

### Blog Post -> Audiogram

**Pull best quote. Generate audio. Add waveform visual.**

```bash
# Generate audio of the key quote
infsh app run falai/dia-tts --input '{
  "prompt": "[S1] The number one mistake I see on landing pages... is putting features above the fold. The best pages show the outcome. Not what your product does, but what life looks like after."
}'
```

### Podcast Episode -> Blog Post

```bash
# 1. Transcribe the episode
infsh app run <stt-app> --input '{
  "audio": "episode-42.mp3"
}'

# 2. Edit transcript into blog format:
# - Remove filler words (um, uh, like, you know)
# - Add headers at topic changes
# - Break into paragraphs
# - Add intro and conclusion
# - Add links mentioned in the episode
```

### Podcast Episode -> Quote Cards

**3-5 best quotes with speaker attribution.**

```bash
# Generate quote card backgrounds
infsh app run falai/flux-dev-lora --input '{
  "prompt": "minimal quote card background, subtle gradient from dark blue to black, large quotation mark watermark, clean modern design, social media square format",
  "width": 1080,
  "height": 1080
}'
```

### Video -> GIF

**Key moment, 3-5 seconds, under 5MB.**

Best moments for GIFs:
- Reaction shots
- Before/after reveals
- Key demonstration steps
- Funny or surprising moments

### Long Video -> Short Clips

```bash
# Extract the best 15-60 second segments for Reels/TikTok/Shorts
# Focus on self-contained moments that make sense without context:
# - A single tip or insight
# - A surprising stat reveal
# - A demonstration of one feature
# - A strong opinion or hot take
```

## The Golden Rule

**Never copy-paste across formats.** Each platform has different:

| Platform | Attention Span | Tone | Format |
|----------|---------------|------|--------|
| Blog | 5-10 min | Thorough, detailed | Long paragraphs OK |
| Twitter/X | 5-30 sec per tweet | Punchy, declarative | 280 chars, fragmented |
| LinkedIn | 1-3 min | Professional, insightful | Short paragraphs, line breaks |
| Newsletter | 5-7 min | Curated, personal | Sections with headers |
| TikTok/Reels | 15-60 sec | Energetic, direct | Hook in 1 second |
| Podcast | 20-60 min | Conversational, deep | Stories and tangents OK |

## Content Repurposing Checklist

For each piece of long-form content, create:

- [ ] Twitter/X thread (5-8 tweets)
- [ ] LinkedIn post or carousel
- [ ] Newsletter section (3-line summary)
- [ ] 2-3 quote cards for social
- [ ] Short-form video script (30-60s)
- [ ] Email snippet for nurture sequence
- [ ] Slide for internal presentation

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Copy-pasting between platforms | Feels lazy, wrong format | Rewrite for each platform's style |
| Repurposing weak content | Amplifies mediocrity | Only repurpose your best pieces |
| Same day posting everywhere | Audience overlap sees duplicates | Stagger across days/weeks |
| Losing the core message | Derivative misses the point | Identify the ONE key insight first |
| No visual adaptation | Text-only on visual platforms | Create platform-specific graphics |
| Forgetting attribution | Plagiarizes yourself | Link back to the original |

## Related Skills

```bash
npx skills add inference-sh/skills@ai-social-media-content
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@text-to-speech
npx skills add inference-sh/skills@twitter-automation
```

Browse all apps: `infsh app list`
