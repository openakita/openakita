---
name: newsletter-curation
description: "Newsletter curation with content sourcing, editorial structure, and subscriber growth strategies. Covers issue formatting, link roundups, commentary style, and sending cadence. Use for: email newsletters, link roundups, weekly digests, curated content, creator newsletters. Triggers: newsletter, email newsletter, newsletter curation, weekly digest, link roundup, curated newsletter, newsletter writing, newsletter format, subscriber growth, newsletter strategy, content curation, newsletter template"
allowed-tools: Bash(infsh *)
---

# Newsletter Curation

Create and curate high-quality newsletters via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Find content to curate
infsh app run tavily/search-assistant --input '{
  "query": "most important AI developments this week 2024"
}'

# Generate newsletter header
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:600px;height:200px;background:linear-gradient(135deg,#1e293b,#334155);display:flex;align-items:center;padding:40px;font-family:system-ui;color:white\"><div><h1 style=\"font-size:32px;margin:0;font-weight:800\">The Weekly Signal</h1><p style=\"font-size:16px;opacity:0.7;margin-top:8px\">Issue #47 — January 15, 2025</p></div></div>"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Newsletter Formats

### 1. Link Roundup

5-15 curated links with 1-3 sentence commentary per link.

```markdown
## This Week's Top Picks

### [Article Title](url)
One to three sentences explaining why this matters and what the
reader will get from it. Add your take — don't just describe.

### [Article Title](url)
Your commentary here. The value is your curation and perspective,
not just the link.
```

### 2. Deep Dive + Links

One in-depth analysis (300-500 words) + 5-8 curated links.

```markdown
## The Big Story

[300-500 word analysis of the week's most important topic]

## Also Worth Reading

- **[Title](url)** — One sentence commentary
- **[Title](url)** — One sentence commentary
...
```

### 3. Original Essay

One focused piece (500-1,000 words) with a clear thesis.

```markdown
## [Essay Title]

[Your original analysis, opinion, or insight]

## What I'm Reading

- [Title](url) — brief note
- [Title](url) — brief note
```

### 4. Q&A / Interview

Feature conversation with an expert or practitioner.

### 5. Data/Trends

Numbers, charts, and analysis of trends in your space.

## Issue Structure

### The Template

```markdown
# [Newsletter Name] — Issue #[N]

## 👋 Hello

[2-3 sentences of personal intro — what's on your mind,
what this issue covers, why it matters right now]

## 🔥 The Big Story

[Featured content — your deepest analysis or most
important curated piece with commentary]

## 📚 Worth Reading

### [Title 1](url)
[2-3 sentence commentary with your take]

### [Title 2](url)
[2-3 sentence commentary]

### [Title 3](url)
[2-3 sentence commentary]

## 💡 Quick Hits

- [One-liner + link](url)
- [One-liner + link](url)
- [One-liner + link](url)

## 📊 Stat of the Week

[One compelling data point with context]

## 💬 From the Community

[Reader reply, question, or discussion point]

---

That's it for this week. If you found this useful, forward
it to a colleague who'd enjoy it.

[Your name]
```

## Content Sourcing

### Where to Find Content

```bash
# Industry news
infsh app run tavily/search-assistant --input '{
  "query": "[your niche] news this week latest developments"
}'

# Research and data
infsh app run exa/search --input '{
  "query": "[your niche] research report statistics 2024"
}'

# Trending discussions
infsh app run tavily/search-assistant --input '{
  "query": "site:reddit.com [your niche] discussion this week"
}'

# Academic/deep content
infsh app run exa/search --input '{
  "query": "[your niche] analysis deep dive opinion"
}'
```

### Source Categories

| Source Type | Examples | Best For |
|------------|---------|----------|
| **News** | TechCrunch, The Verge, industry press | Breaking developments |
| **Research** | Papers, reports, surveys | Data-backed insights |
| **Blogs** | Engineering blogs, personal blogs | Practitioner perspectives |
| **Social** | Twitter threads, LinkedIn posts | Hot takes, discussions |
| **Tools** | Product launches, updates | Practical recommendations |
| **Community** | Reddit, HN, forums | Ground-level sentiment |

### Curation Quality Filter

For each piece of content, ask:

| Question | If No → |
|---------|---------|
| Would I send this to a colleague 1-on-1? | Don't include |
| Does it teach something actionable? | Consider skipping |
| Is the source credible? | Find better source |
| Is it timely/relevant this week? | Save for later or skip |
| Can I add commentary that adds value? | Just linking isn't enough |

## Writing Commentary

### What Makes Good Commentary

```
❌ Just describing: "This article talks about React Server Components."
❌ Restating the headline: "React Server Components are here."

✅ Adding context: "React Server Components shipped last week, and this
   is the first production teardown I've seen. Key insight: they reduced
   initial JS bundle by 60%, but added complexity to the build pipeline."

✅ Giving your take: "I'm skeptical about the migration path here.
   Most teams I've talked to are waiting for better tooling."

✅ Connecting dots: "This pairs well with Vercel's announcement last
   month — the ecosystem is clearly converging on this pattern."
```

### Commentary Formula

```
[What happened] + [Why it matters to the reader] + [Your take or prediction]
```

## Sending Cadence

| Frequency | Best For | Open Rate Impact |
|-----------|---------|-----------------|
| **Weekly** | Most newsletters | Highest — predictable, not overwhelming |
| **Bi-weekly** | Deep analysis, essays | Good if content is substantial |
| **Daily** | News-focused, short format | Requires dedicated habit, risky |
| **Monthly** | Research roundups | OK for depth, risks being forgotten |

**Weekly is the sweet spot.** Same day, same time, every week. Consistency builds habit.

| Day | Performance |
|-----|------------|
| Tuesday | Highest open rates |
| Thursday | Second highest |
| Wednesday | Third |
| Monday | Lower (inbox overload) |
| Friday | Lower (weekend mode) |
| Weekend | Lowest (but some niches thrive) |

## Subject Lines

| Formula | Example |
|---------|---------|
| Issue number + teaser | "#47: The framework nobody's talking about" |
| Number + topic | "5 tools that changed my workflow this month" |
| Question | "Is TypeScript dying?" |
| This week + category | "This week in AI: GPT-5 rumors, open source wins" |
| Direct value | "The SQL optimization guide I wish I had earlier" |

**Keep under 50 characters.** Mobile truncates at ~35.

## Growth Strategies

| Strategy | Implementation |
|----------|---------------|
| **Cross-promotion** | Partner with complementary newsletters |
| **Social distribution** | Post key insights on Twitter/LinkedIn with subscribe CTA |
| **Referral program** | "Forward to 3 friends" or formal referral rewards |
| **SEO archive** | Publish newsletter archive as blog posts |
| **Lead magnet** | "Subscribe and get [free resource]" |
| **Consistent quality** | The best growth strategy: be worth reading |

```bash
# Create social teaser for newsletter
infsh app run x/post-create --input '{
  "text": "This week in The Weekly Signal:\n\n→ Why edge computing is eating the backend\n→ The database migration nobody talks about\n→ 5 tools I discovered this month\n\nJoin 2,000+ engineers: [link]\n\nIssue #47 drops tomorrow morning."
}'
```

## Metrics That Matter

| Metric | Good | Great | Action If Low |
|--------|------|-------|--------------|
| **Open rate** | 30-40% | 40%+ | Improve subject lines |
| **Click rate** | 3-5% | 5%+ | Better content curation, stronger CTAs |
| **Unsubscribe rate** | < 0.5% per issue | < 0.2% | Check content quality, frequency |
| **Reply rate** | Any replies | Regular replies | Ask questions, invite conversation |
| **Forward rate** | Any forwards | — | Make content share-worthy |
| **Growth rate** | 5-10% monthly | 10%+ | Increase distribution, referral program |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| No consistent schedule | Readers forget about you | Same day, same time, every week |
| Links without commentary | You're a bookmark, not a newsletter | Add your take on every piece |
| Too many links (15+) | Overwhelming, nothing stands out | 5-10 curated picks max |
| Generic subject lines | Low open rates | Tease the best content, keep under 50 chars |
| No personal voice | Reads like an RSS feed | Intro paragraph, opinions, personality |
| Only promotional content | Readers unsubscribe | 90% value, 10% promotion max |
| Inconsistent quality | Trust erodes | Skip an issue rather than send a weak one |
| No CTA for engagement | One-way broadcast | Ask questions, invite replies, encourage forwards |
| No archive/SEO | Missing growth channel | Publish issues as web pages |

## Related Skills

```bash
npx skills add inference-sh/skills@email-design
npx skills add inference-sh/skills@content-repurposing
npx skills add inference-sh/skills@seo-content-brief
```

Browse all apps: `infsh app list`
