---
name: linkedin-content
description: "LinkedIn post writing with hook formulas, formatting rules, and engagement patterns. Covers post types, algorithm signals, character limits, and content pillars. Use for: LinkedIn posts, professional content, thought leadership, B2B content, personal branding. Triggers: linkedin post, linkedin content, linkedin writing, linkedin strategy, linkedin engagement, linkedin algorithm, linkedin hook, linkedin formatting, thought leadership, professional content, b2b content, linkedin growth"
allowed-tools: Bash(infsh *)
---

# LinkedIn Content

Write high-engagement LinkedIn posts via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Research trending LinkedIn content patterns
infsh app run tavily/search-assistant --input '{
  "query": "LinkedIn viral post examples 2024 high engagement patterns"
}'

# Post to X (cross-posting reference)
infsh app run x/post-create --input '{
  "text": "Your cross-posted version here"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Post Anatomy

```
┌─────────────────────────────────────┐
│ HOOK (first 1-2 lines)             │ ← Visible before "...see more"
│                                     │
│ ...see more ─────────────────────── │ ← The click gate
│                                     │
│ BODY (story/value)                  │
│ - Formatted with line breaks        │
│ - Short paragraphs (1-2 sentences)  │
│ - Lists or numbered points          │
│                                     │
│ CTA (last 1-2 lines)              │ ← Ask for engagement
│                                     │
│ #hashtags (3-5)                     │
└─────────────────────────────────────┘
```

## Character Limits

| Element | Limit |
|---------|-------|
| Post text | 3,000 characters |
| Visible before "see more" | ~210 characters (~2 lines on mobile) |
| Hashtags | 3-5 recommended |
| Comment | 1,250 characters |
| Article title | 100 characters |
| Article body | 125,000 characters |

**The first 210 characters are everything.** If the hook fails, nobody clicks "see more."

## Hook Formulas

### What Works

| Formula | Example |
|---------|---------|
| Contrarian opinion | "Unpopular opinion: code reviews are a waste of time." |
| Personal story opening | "I got fired on a Tuesday. Best thing that ever happened." |
| Surprising stat | "92% of startups fail. But not for the reason you think." |
| List promise | "I've hired 200+ engineers. Here are 5 red flags I look for." |
| Bold statement | "Your resume doesn't matter. Here's what does." |
| Before/after | "3 years ago I couldn't get a single interview. Yesterday I turned down a FAANG offer." |
| Pattern interrupt | "Stop. Before you send that cold email, read this." |

### What Fails

```
❌ "Excited to announce that we are pleased to share..." (corporate speak)
❌ "In today's rapidly evolving landscape..." (cliché, says nothing)
❌ "I'd like to take a moment to..." (slow, no hook)
❌ "Just published a new blog post!" (no value proposition)
❌ Starting with a hashtag or emoji
```

## Formatting Rules

### Line Breaks Are Your Best Friend

```
❌ Dense paragraph:
"I learned something important about leadership last week. My team was struggling with a deadline and instead of pushing harder, I decided to remove scope. The result was incredible — we shipped faster and the quality was better. Sometimes less really is more."

✅ Formatted for LinkedIn:
"I learned something about leadership last week.

My team was struggling with a deadline.

Instead of pushing harder, I removed scope.

The result?

We shipped faster.
And the quality was BETTER.

Sometimes less really is more."
```

### Formatting Guidelines

| Rule | Why |
|------|-----|
| One sentence per line | Easier to scan on mobile |
| Blank line between paragraphs | Visual breathing room |
| Short paragraphs (1-2 sentences) | Mobile readability |
| Use line breaks for dramatic effect | Creates pacing and suspense |
| Bold key phrases sparingly | Draws eye to important points |
| Numbered lists for tips | Scannable, shareable |
| Avoid walls of text | Nobody reads them |

## Post Types (Ranked by Engagement)

| Post Type | Engagement | Best For |
|-----------|-----------|----------|
| **Personal story + lesson** | Very High | Building connection, authenticity |
| **Contrarian take** | High | Starting conversations, visibility |
| **Carousel (document post)** | High | Educational content, tips |
| **List/tips (numbered)** | High | Actionable value, saves |
| **Poll** | Medium-High | Easy engagement, data gathering |
| **Photo + story** | Medium | Humanizing, events |
| **Video (native)** | Medium | Demonstrations, personality |
| **Link post** | Low | Driving traffic (algorithm penalizes) |
| **Reshare** | Very Low | Don't bother — write original |

### Link Posts Strategy

LinkedIn penalizes posts with links (reduces reach). Workarounds:

1. **Comment method**: Post without link, add link as first comment, edit post to say "Link in comments"
2. **No-link method**: Summarize the content in the post itself, mention "DM for link"
3. **If you must link**: Put it at the very end, after strong standalone content

## Content Pillars

Every LinkedIn creator should have 3-5 pillars they rotate through:

| Pillar | What It Covers | Example |
|--------|---------------|---------|
| **Expertise** | Industry knowledge, how-tos | "5 database patterns every engineer should know" |
| **Stories** | Personal experiences, failures, wins | "The hardest feedback I ever received" |
| **Opinions** | Takes on industry trends, contrarian views | "AI won't replace engineers. Bad managers will." |
| **Behind the scenes** | Building in public, process | "Here's our actual sprint retrospective format" |
| **Curated insights** | Trends, data, research summaries | "I analyzed 500 job postings. Here's what changed." |

## Algorithm Signals

| Signal | Impact | How |
|--------|--------|-----|
| **Dwell time** | Very High | Longer posts that people read fully |
| **Comments** | Very High | Ask questions, create discussion |
| **Saves** | High | Actionable, reference-worthy content |
| **"See more" clicks** | High | Strong hook that makes people expand |
| **Shares** | Medium | Relatable, quotable content |
| **Reactions** | Medium | Easy to get but weighted less |
| **External links** | Negative | Reduces reach — put links in comments |
| **Editing after posting** | Negative | Don't edit within first hour |
| **Posting frequency** | 3-5x/week | Daily is fine, more than 1/day hurts |

## Posting Schedule

| Day | Best Time (your audience's timezone) |
|-----|------|
| Tuesday-Thursday | 7-8 AM, 12 PM, 5-6 PM |
| Monday | 8 AM (people catching up) |
| Friday | 7-8 AM (before checkout) |
| Weekend | Skip or light content |

**Engage in comments for 30-60 minutes after posting** — this is more important than the post itself.

## Visual Content

```bash
# Generate a visual for a LinkedIn post
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1080px;height:1080px;background:#0f172a;display:flex;align-items:center;justify-content:center;padding:80px;font-family:system-ui;color:white;text-align:center\"><div><h1 style=\"font-size:56px;font-weight:900;line-height:1.2;margin:0\">The best code is the code you don&apos;t write</h1><p style=\"font-size:22px;opacity:0.5;margin-top:24px\">— Every senior engineer</p></div></div>"
}'

# Generate a professional photo for a personal post
infsh app run falai/flux-dev-lora --input '{
  "prompt": "candid professional photo, person speaking at a conference podium, audience in background blurred, natural stage lighting, authentic moment, corporate event photography",
  "width": 1200,
  "height": 900
}'
```

## CTA Formulas

End every post with engagement driver:

| CTA Type | Example |
|----------|---------|
| Question | "What's the worst career advice you've received?" |
| Agreement check | "Agree or disagree?" |
| Share request | "Repost if this resonates ♻️" |
| Save prompt | "Save this for your next [situation] 🔖" |
| Recommendation ask | "What would you add to this list?" |
| Experience ask | "Has this happened to you?" |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Weak hook | Nobody clicks "see more" | Use hook formulas above |
| Wall of text | Unreadable on mobile | One sentence per line, blank lines between |
| Links in main post | Algorithm reduces reach | Put links in first comment |
| Too many hashtags | Looks spammy | 3-5 relevant hashtags max |
| Corporate jargon | "Leveraging synergies" = instant scroll past | Write like you talk |
| Only self-promotion | Audience stops engaging | 80% value, 20% promotion |
| No CTA | No engagement direction | Always end with a question or ask |
| Resharing without adding | Near-zero reach | Write original posts, quote instead |
| Posting and disappearing | Kills comment momentum | Engage for 30-60 min after posting |
| Being generic | "Hard work pays off" = invisible | Specific stories and data |

## Related Skills

```bash
npx skills add inference-sh/skills@social-media-carousel
npx skills add inference-sh/skills@content-repurposing
npx skills add inference-sh/skills@twitter-thread-creation
```

Browse all apps: `infsh app list`
