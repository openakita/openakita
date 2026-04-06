---
name: press-release-writing
description: "Press release writing in AP style with inverted pyramid structure. Covers formatting, datelines, quotes, boilerplates, and fact-checking. Use for: product launches, funding announcements, partnerships, company news, events. Triggers: press release, pr writing, media release, news release, announcement, product launch announcement, funding announcement, company news, media advisory, ap style, press statement, news wire"
allowed-tools: Bash(infsh *)
---

# Press Release Writing

Write professional press releases with research and fact-checking via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Research for fact-checking and context
infsh app run tavily/search-assistant --input '{
  "query": "SaaS funding rounds Q1 2024 average series A size"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## AP Style Format

### Structure

```
HEADLINE IN TITLE CASE, PRESENT TENSE, NO PERIOD
Optional Subheadline With More Detail

CITY, STATE (Month Day, Year) — Lead paragraph with WHO, WHAT, WHEN,
WHERE, and WHY in the first 25 words.

Second paragraph expands on the lead with supporting details, context,
and significance.

"Executive quote providing perspective on the announcement," said
[Full Name], [Title] at [Company]. "Second sentence of quote adding
depth or forward-looking statement."

Body paragraphs with additional details, arranged in descending order
of importance (inverted pyramid).

"Supporting quote from partner, customer, or analyst," said
[Full Name], [Title] at [Organization].

Final paragraph with availability, pricing, or next steps.

About [Company]
[Company] is a [description]. Founded in [year], the company
[brief background]. For more information, visit [website].

Media Contact:
[Name]
[Email]
[Phone]
```

## Section-by-Section Guide

### Headline

```
❌ Company X Announces Revolutionary New Product That Will Change Everything!
❌ Press Release: Company X
❌ Company X's Amazing Product Launch

✅ Company X Launches AI-Powered Analytics Platform for Enterprise Teams
✅ Company X Raises $25 Million Series B to Expand Global Operations
✅ Company X Partners With Acme Corp to Accelerate Cloud Migration
```

**Rules:**
- Present tense, active voice
- No period at end
- No superlatives ("revolutionary", "groundbreaking", "best-in-class")
- No exclamation points
- Include the key news element
- Title case

### Dateline

```
SAN FRANCISCO, Jan. 15, 2026 —
NEW YORK, March 3, 2026 —
LONDON, Dec. 10, 2026 —
```

**AP month abbreviations:** Jan., Feb., Aug., Sept., Oct., Nov., Dec. (March, April, May, June, July spelled out)

### Lead Paragraph

Answer WHO, WHAT, WHEN, WHERE, WHY in 25-35 words:

```
❌ "We are thrilled to announce that after months of hard work, our talented
    team has created something truly special that we think you'll love."

✅ "Company X, a developer tools startup, today launched DataFlow, an
    AI-powered analytics platform that automates reporting for enterprise
    engineering teams."
```

### Quotes

**Rules:**
- 1-2 quotes maximum (CEO/founder + partner/customer)
- Never start a quote with "I"
- Attribution format: "Quote," said Full Name, Title at Company.
- Quotes should add perspective, not repeat facts from the body
- Forward-looking quotes work well: "We believe this will..."

```
❌ "I am so excited about this launch," said John Smith.
❌ "We launched a new product today," said the CEO.

✅ "Enterprise teams spend an average of 15 hours per week on manual
    reporting," said Sarah Chen, CEO of Company X. "DataFlow eliminates
    that burden entirely, letting engineers focus on building."

✅ "Since adopting DataFlow, our reporting cycle dropped from three days
    to three minutes," said Marcus Lee, VP of Engineering at Acme Corp.
```

### Boilerplate (About Section)

```
About Company X
Company X is a [category] company that [what it does] for [who].
Founded in [year] and headquartered in [city], the company serves
[number] customers across [industries/geographies]. For more
information, visit www.companyx.com.
```

Keep to 3-4 sentences. Consistent across all press releases.

### Media Contact

```
Media Contact:
Jane Doe
PR Manager, Company X
jane@companyx.com
(555) 123-4567
```

## The Inverted Pyramid

Most important information first. Each paragraph is less critical than the one before. Editors cut from the bottom.

```
┌─────────────────────────┐
│    MOST IMPORTANT       │  Lead: core announcement
│    (Who, What, When,    │
│     Where, Why)         │
├─────────────────────────┤
│  IMPORTANT DETAILS      │  Supporting facts, context
│  (How, stats, quotes)   │
├─────────────────────────┤
│  BACKGROUND             │  Industry context, history
│  (Context, trends)      │
├─────────────────────────┤
│  ADDITIONAL INFO        │  Availability, pricing
│  (Nice to have)         │
├─────────────────────────┤
│  BOILERPLATE            │  About section, contact
└─────────────────────────┘
```

## Research & Fact-Checking

### Verify Claims

```bash
# Check market size claims
infsh app run tavily/search-assistant --input '{
  "query": "enterprise analytics market size 2024 2025 forecast"
}'

# Verify competitor claims
infsh app run exa/search --input '{
  "query": "Company X competitors enterprise analytics market share"
}'

# Get industry statistics
infsh app run exa/answer --input '{
  "question": "How much time do engineering teams spend on reporting weekly?"
}'
```

### Add Context

```bash
# Industry trends for the "why now" angle
infsh app run tavily/search-assistant --input '{
  "query": "AI automation enterprise reporting trends 2024"
}'
```

## Press Release Types

### Product Launch

**Focus:** What it does, who it's for, why it matters, availability
**Quote:** CEO or product lead on the vision

### Funding Announcement

**Focus:** Amount, round, lead investor, what funds will be used for
**Quote:** CEO on growth plans + lead investor on why they invested

### Partnership

**Focus:** What the partnership enables, benefits to customers
**Quote:** One from each company

### Milestone / Achievement

**Focus:** The metric, growth trajectory, what it means
**Quote:** CEO on the journey and what's next

### Executive Hire

**Focus:** Who, their background, what they'll lead
**Quote:** CEO on why this hire + new exec on why they joined

## Length Guidelines

| Element | Length |
|---------|--------|
| Headline | 10-15 words |
| Subheadline (optional) | 15-25 words |
| Total body | 400-600 words |
| Quotes | 2-3 sentences each, max 2 quotes |
| Boilerplate | 3-4 sentences |
| **Total** | **500-800 words** |

Over 800 words and editors won't read it. Under 400 and it lacks substance.

## AP Style Quick Reference

| Rule | Example |
|------|---------|
| Numbers 1-9 spelled out, 10+ as digits | "nine employees" / "10 employees" |
| Percent as one word | "15 percent" (not 15% in body text) |
| Titles before names capitalized | "CEO Sarah Chen" |
| Titles after names lowercase | "Sarah Chen, chief executive officer" |
| Company names: no Inc./Corp. in body | "Company X" not "Company X, Inc." |
| Dates: month day, year | "Jan. 15, 2026" |
| States abbreviated in dateline | "SAN FRANCISCO, Calif." |
| Serial comma: AP does NOT use it | "fast, simple and effective" |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Superlatives | "Revolutionary" = ignored by editors | State facts, let readers judge |
| Exclamation points | Unprofessional | Never use in press releases |
| Starting quotes with "I" | Informal, weak opening | Start with a fact or insight |
| Burying the lead | Key news in paragraph 3 | Most important info first |
| Too long | Won't be read | 500-800 words max |
| Jargon | Alienates non-expert readers | Write for a general audience |
| No fact-checking | Credibility risk | Verify all claims and statistics |
| Missing contact info | Journalists can't follow up | Always include media contact |

## Checklist

- [ ] Headline: present tense, active voice, no period, no superlatives
- [ ] Dateline: correct AP format (CITY, STATE, date)
- [ ] Lead: WHO, WHAT, WHEN, WHERE, WHY in first 25 words
- [ ] Inverted pyramid: most important first
- [ ] Quotes: attributed, don't start with "I", add perspective
- [ ] All claims and statistics fact-checked
- [ ] Boilerplate: consistent with other releases
- [ ] Media contact: name, email, phone
- [ ] 500-800 words total
- [ ] Read aloud for flow

## Related Skills

```bash
npx skills add inference-sh/skills@web-search
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
