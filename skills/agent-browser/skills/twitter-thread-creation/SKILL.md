---
name: twitter-thread-creation
description: "Twitter/X thread writing with hook tweets, thread structure, and engagement optimization. Covers tweet formatting, character limits, media attachments, and posting strategies. Use for: Twitter threads, X posts, tweet storms, Twitter content, social media writing. Triggers: twitter thread, tweet thread, x thread, twitter post, tweet writing, thread creation, tweet storm, twitter content, x post, twitter writing, twitter hook, tweet formatting, thread structure"
allowed-tools: Bash(infsh *)
---

# Twitter/X Thread Creation

Write high-engagement Twitter/X threads via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Post a tweet
infsh app run x/post-create --input '{
  "text": "I analyzed 1,000 landing pages.\n\n90% make the same 5 mistakes.\n\nHere are the fixes (with examples):\n\n🧵👇"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Character Limits

| Element | Limit |
|---------|-------|
| Tweet text | 280 characters (free), 25,000 (Premium) |
| Thread length | No limit (10-15 tweets is sweet spot) |
| Image alt text | 1,000 characters |
| Quote tweet addition | 280 characters |
| Reply | 280 characters |
| Display name | 50 characters |

## Thread Structure

### The Anatomy

```
Tweet 1 (Hook):     Bold claim + "thread 🧵"
Tweet 2:            Context / why this matters
Tweet 3-9:          One point per tweet (numbered)
Tweet 10:           Summary or biggest takeaway
Tweet 11:           CTA (follow, retweet, bookmark)
```

### Tweet 1: The Hook

This tweet lives or dies alone in the timeline. It must work WITHOUT the thread.

| Hook Type | Template |
|-----------|----------|
| I did X + result | "I analyzed 1,000 [things]. Here's what I found:" |
| Number + list | "10 [topic] tips that [benefit]:" |
| Contrarian | "Unpopular opinion: [bold take]" |
| Story opener | "In 2019, I [dramatic event]. Here's what happened:" |
| How-to promise | "How to [achieve outcome] (step by step):" |
| Surprising fact | "[Stat that seems wrong]. Let me explain:" |

```bash
# Post hook tweet
infsh app run x/post-create --input '{
  "text": "I spent 3 years building SaaS products.\n\nHere are 10 things I wish someone told me on day 1:\n\n🧵"
}'
```

### Content Tweets (3-9)

| Rule | Why |
|------|-----|
| One idea per tweet | Clarity and retweetability |
| Number them (1/, 2/, etc.) | Progress signal, easy to reference |
| Each tweet should stand alone | People share individual tweets |
| Lead with the insight | Don't bury the point |
| Use line breaks | Visual breathing room |
| Include examples | Abstract → concrete |

```bash
# Content tweet with visual
infsh app run x/post-create --input '{
  "text": "3/ Your pricing page is the second most visited page on your site.\n\nBut most founders treat it as an afterthought.\n\nThe fix:\n→ Show 3 tiers (not 2, not 5)\n→ Highlight the middle one\n→ Annual toggle defaulted ON\n→ Feature comparison below"
}'
```

### Closing Tweet

```bash
# CTA tweet
infsh app run x/post-create --input '{
  "text": "11/ That'\''s the full playbook.\n\nTL;DR:\n• Validate before building\n• Launch ugly, iterate fast\n• Pricing is positioning\n• Talk to users weekly\n\nIf this was useful:\n→ Retweet the first tweet\n→ Follow me @username for more\n→ Bookmark this thread"
}'
```

## Formatting Rules

### Tweet Formatting

```
❌ Dense:
"If you want to grow on Twitter you need to post consistently and engage with your audience while also making sure your content provides value to your followers."

✅ Formatted:
"Want to grow on Twitter?

3 non-negotiable rules:

→ Post daily (consistency > quality)
→ Reply to 20 accounts bigger than you
→ Every tweet must teach OR entertain

No shortcuts."
```

### Symbols for Lists

| Symbol | Use For |
|--------|---------|
| → | Steps, actions, directions |
| • | Bullet points, lists |
| — | Asides, attributions |
| ✅ | Do's, positives |
| ❌ | Don'ts, negatives |
| 1/ 2/ 3/ | Numbered thread tweets |

### Line Break Strategy

```
Short sentence.
                    ← blank line
Short sentence.
                    ← blank line
Punchline.
```

Line breaks create **pacing**. Use them to control reading speed and emphasis.

## Media in Threads

### When to Add Images

| Tweet Position | Image Type | Purpose |
|---------------|-----------|---------|
| Hook (tweet 1) | Eye-catching graphic | Stop the scroll |
| Key points | Screenshots, examples | Evidence |
| Summary | Infographic | Shareable recap |

```bash
# Generate thread header image
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1200px;height:675px;background:linear-gradient(135deg,#0f172a,#1e293b);display:flex;align-items:center;justify-content:center;padding:60px;font-family:system-ui;color:white;text-align:center\"><div><h1 style=\"font-size:48px;font-weight:900;line-height:1.2;margin:0\">10 SaaS Pricing Mistakes<br>That Cost You Revenue</h1><p style=\"font-size:22px;opacity:0.5;margin-top:20px\">A thread 🧵</p></div></div>"
}'

# Generate screenshots for evidence
infsh app run infsh/agent-browser --input '{
  "url": "https://example.com/pricing",
  "action": "screenshot"
}'
```

### Image Specs

| Format | Dimensions | Max Size |
|--------|-----------|----------|
| Single image | 1200 x 675 (16:9) recommended | 5 MB |
| Two images | 700 x 800 each | 5 MB each |
| Four images | 600 x 600 each | 5 MB each |
| GIF | 1280 x 1080 max | 15 MB |

## Thread Types

### Educational

```
1/ [Topic] explained simply:
2/ What is [concept]?
3/ Why it matters
4-8/ Key principles (numbered)
9/ Common mistakes
10/ Resources
11/ CTA
```

### Story/Journey

```
1/ [Dramatic opener]
2/ Background/context
3-7/ Chronological events
8/ The turning point
9/ The lesson
10/ How to apply it
11/ CTA
```

### Curation/List

```
1/ [Number] [things] every [audience] needs:
2-10/ One item per tweet with brief explanation
11/ CTA
```

### Teardown/Analysis

```
1/ I analyzed [thing]. Here's what I found:
2/ The setup (what I looked at)
3-8/ Finding 1, 2, 3... with evidence
9/ The biggest surprise
10/ Takeaways
11/ CTA
```

## Engagement Strategy

| Action | Timing | Why |
|--------|--------|-----|
| Post hook tweet | Peak hours (8-10 AM, 12-1 PM your audience's TZ) | Maximum initial visibility |
| Reply-chain the thread | Immediately after hook | Complete the thread |
| Pin the thread | Right after posting | Visitors see your best work |
| Engage with replies | First 60 minutes | Algorithm boost |
| Quote-tweet highlight | Next day | Second wave of visibility |
| Repost hook | 1-2 weeks later | Catch new followers |

## Repurposing to Thread

```bash
# Research source material
infsh app run tavily/search-assistant --input '{
  "query": "latest statistics on remote work productivity 2024"
}'

# Generate visual for the thread
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1200px;height:675px;background:#0f172a;display:flex;align-items:center;padding:60px;font-family:system-ui;color:white\"><div><p style=\"font-size:20px;color:#38bdf8;text-transform:uppercase;letter-spacing:2px\">Data Deep Dive</p><h1 style=\"font-size:52px;font-weight:900;margin:12px 0;line-height:1.2\">Remote Work in 2024:<br>What the Data Actually Says</h1></div></div>"
}'
```

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Weak hook tweet | Thread dies at tweet 1 | Use hook formulas — bold, specific, curiosity-driving |
| Too many tweets (20+) | Readers drop off after 10-12 | Sweet spot is 8-12 tweets |
| Multiple ideas per tweet | Confusing, not retweetable | One idea = one tweet |
| No numbers on tweets | Hard to follow, no progress signal | Always number: 1/, 2/, 3/ |
| No images | Threads with images get 2x engagement | Add visuals to hook + key points |
| Thread only (no standalone) | Miss the non-thread audience | Post standalone tweets too, not just threads |
| No CTA at the end | Missed follow/engagement opportunity | Always ask to RT, follow, bookmark |
| Posting at wrong time | Low initial engagement kills reach | Post during your audience's peak hours |
| Wall of text tweets | Nobody reads dense tweets | Line breaks, symbols, short sentences |

## Related Skills

```bash
npx skills add inference-sh/skills@linkedin-content
npx skills add inference-sh/skills@content-repurposing
npx skills add inference-sh/skills@social-media-carousel
```

Browse all apps: `infsh app list`
