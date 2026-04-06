---
name: product-hunt-launch
description: "Product Hunt launch optimization with specific specs, timing, and gallery strategy. Covers taglines, gallery images, maker comments, and launch day tactics. Use for: product launches, startup launches, side project launches, Product Hunt optimization. Triggers: product hunt, ph launch, product hunt launch, launch strategy, product launch, startup launch, product hunt tips, product hunt gallery, ph optimization, launch day, product hunt maker"
allowed-tools: Bash(infsh *)
---

# Product Hunt Launch

Optimize your Product Hunt launch with research and visuals via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate gallery hero image
infsh app run falai/flux-dev-lora --input '{
  "prompt": "clean product showcase, modern SaaS dashboard interface on laptop screen, floating UI elements around it, soft gradient background from blue to purple, professional marketing hero shot, minimal clean design",
  "width": 1248,
  "height": 832
}'

# Research competitor launches
infsh app run tavily/search-assistant --input '{
  "query": "Product Hunt top launches this week SaaS tools"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Listing Specifications

| Element | Spec | Notes |
|---------|------|-------|
| Product name | — | Keep it short, memorable |
| Tagline | **60 character limit** | No period at end |
| Description | First **260 chars** show in preview | Full description can be longer |
| Gallery images | Up to **8 images** | 1270 x 760 px recommended |
| Topics | **Max 3** | Pick the most specific ones |
| Makers | Tag all team members | They can engage in comments |
| Link | Product URL | Where upvoters go |

## Gallery Images

### The First Image Is Everything

The first gallery image shows in the feed, email digest, and social shares. It IS your first impression.

| Position | Content | Goal |
|----------|---------|------|
| **1 (Hero)** | Product in action, core value visible | Stop the scroll, communicate what it does |
| **2** | Key feature demonstration | Show the "aha moment" |
| **3** | Before/after or problem/solution | Show the transformation |
| **4** | Social proof or metrics | Build credibility |
| **5** | Technical differentiator or integrations | For evaluators |

### Dimensions

- Recommended: **1270 x 760 px** (16:9 ish)
- Minimum: 600px wide
- Supports GIF for animated demos

### Generating Gallery Images

```bash
# Image 1: Hero product shot
infsh app run falai/flux-dev-lora --input '{
  "prompt": "modern SaaS product showcase, clean dashboard interface floating above gradient background, UI showing analytics charts and metrics, professional product marketing style, soft shadows, blue and white color scheme, wide format",
  "width": 1248,
  "height": 832
}'

# Image 2: Feature demo
infsh app run falai/flux-dev-lora --input '{
  "prompt": "product feature showcase, split screen showing drag-and-drop interface on left and generated output on right, clean UI design, modern SaaS aesthetic, subtle grid background, professional marketing",
  "width": 1248,
  "height": 832
}'

# Image 3: Before/after
infsh app run infsh/stitch-images --input '{
  "images": ["before-state.png", "after-state.png"],
  "direction": "horizontal"
}'

# Image 4: Social proof / metrics
infsh app run falai/flux-dev-lora --input '{
  "prompt": "clean infographic style image showing upward growth metrics, large numbers and charts on dark background, professional data visualization, startup metrics dashboard style, modern minimal design",
  "width": 1248,
  "height": 832
}'
```

## Tagline

60 characters. No period. Must communicate what the product does AND why someone should care.

### Formulas That Work

| Formula | Example |
|---------|---------|
| [Action] for [audience] | "AI writing assistant for developers" |
| [Result] without [pain] | "Beautiful docs without the design skills" |
| [Tool] that [benefit] | "Analytics that explain themselves" |
| [Adjective] [category] | "Instant customer feedback surveys" |
| The [category] for [niche] | "The Figma for data visualization" |

### Examples

```
❌ "The best project management tool ever created" (superlative, 52 chars)
❌ "We help you manage projects better." (vague, has period, 37 chars)
❌ "AI-powered machine learning project management SaaS" (buzzword soup)

✅ "Ship docs in minutes, not days" (31 chars)
✅ "AI turns your data into stories" (32 chars)
✅ "The open-source Calendly alternative" (37 chars)
```

## Timing

### When to Launch

| Factor | Recommendation |
|--------|----------------|
| Day | **Tuesday, Wednesday, or Thursday** (highest traffic) |
| Time | **12:01 AM PT** (Pacific Time) — start of the PH day |
| Avoid | Weekends, holidays, major Apple/Google events |
| Duration | PH day = midnight PT to midnight PT |

### Why 12:01 AM PT

- Maximum time in the running for daily top spot
- Accumulate upvotes throughout the full day
- Morning email digest (US time) includes your product
- Allows engagement across all US time zones

## Maker Comment

Post within **5 minutes** of going live. This is your pitch.

### Structure

```
Hey Product Hunt! 👋

[1 sentence: what it is]

[2-3 sentences: why you built it / the problem you noticed]

[1-2 sentences: how it works / key differentiator]

[1 sentence: what's next / what you're looking for]

Would love to hear your thoughts — happy to answer any questions!
```

### Example

```
Hey Product Hunt!

DataFlow turns raw SQL queries into visual dashboards in seconds.

As a data engineer, I was frustrated spending more time formatting
reports than actually analyzing data. Every BI tool I tried required
a PhD in their configuration. So I built DataFlow — paste your SQL,
get a dashboard.

It auto-detects chart types, handles large datasets, and exports
to PDF/PNG with one click.

We're offering 50% off the first year for PH users. Would love
your feedback — what reporting pain points do you have?
```

## Launch Day Playbook

### Before Launch (1-2 weeks)

- [ ] Gallery images finalized (5 recommended)
- [ ] Tagline tested with 5+ people (do they understand what it does?)
- [ ] Maker comment drafted and proofread
- [ ] Landing page with PH badge ready
- [ ] Early supporter list ready (people who want to check it out)
- [ ] Social media announcement posts drafted

### Launch Day Timeline

| Time (PT) | Action |
|-----------|--------|
| 12:01 AM | Product goes live, post maker comment immediately |
| 12:15 AM | Share on personal social media |
| 6:00 AM | First engagement check — reply to all comments |
| 9:00 AM | Share in relevant communities (naturally, not spammy) |
| 12:00 PM | Mid-day check — reply to all new comments |
| 3:00 PM | Share any early traction or interesting feedback |
| 6:00 PM | Evening engagement — reply to remaining comments |
| 11:59 PM | Day ends — results are final |

### Engagement Rules

- **Reply to every comment** — makers who engage get more visibility
- **Ask questions back** — creates conversation threads
- **Be genuine** — don't use canned responses
- **Never ask for upvotes** — against PH terms of service
- **Share the link naturally** — "Check it out at [url]" not "Please upvote"

## Research for Preparation

```bash
# Study similar product launches
infsh app run tavily/search-assistant --input '{
  "query": "Product Hunt top launches analytics tools best practices"
}'

# Competitive landscape
infsh app run exa/search --input '{
  "query": "Product Hunt analytics dashboard tools launched 2024 2025"
}'

# Community sentiment
infsh app run tavily/search-assistant --input '{
  "query": "Product Hunt launch tips what works 2024 maker advice"
}'
```

## Post-Launch

| When | Action |
|------|--------|
| Day 1-3 | Reply to all remaining comments, thank supporters |
| Week 1 | Publish a "lessons learned" blog post / Twitter thread |
| Week 2 | Follow up with interested users from comments |
| Month 1 | Check if you're eligible for "Product of the Week/Month" |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Launching on Friday/weekend | Low traffic, wasted launch | Tue-Thu only |
| Launching at noon | Half the day already gone | 12:01 AM PT |
| No maker comment | Looks abandoned | Post within 5 minutes |
| Asking for upvotes | Against TOS, can get flagged | Share naturally, let product speak |
| Generic gallery images | Doesn't show the product | Show real UI, real features |
| Not replying to comments | Low engagement signal | Reply to every single comment |
| Too many topics | Dilutes discoverability | Max 3, pick the most specific |
| Tagline with buzzwords | Nobody knows what you do | Clear, specific, benefit-focused |

## Related Skills

```bash
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@web-search
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
