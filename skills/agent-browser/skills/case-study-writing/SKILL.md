---
name: case-study-writing
description: "B2B case study writing with STAR framework, data visualization, and research. Covers structure, customer quotes, metrics presentation, and distribution formats. Use for: customer success stories, portfolio pieces, sales enablement, marketing content. Triggers: case study, customer story, success story, b2b case study, client testimonial, customer case study, portfolio case study, use case, customer win, results story"
allowed-tools: Bash(infsh *)
---

# Case Study Writing

Create compelling B2B case studies with research and visuals via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Research the customer's industry
infsh app run tavily/search-assistant --input '{
  "query": "SaaS customer onboarding challenges 2024 statistics"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## The STAR Framework

Every case study follows: **Situation -> Task -> Action -> Result**

| Section | Length | Content | Purpose |
|---------|--------|---------|---------|
| **Situation** | 100-150 words | Who the customer is, their context | Set the scene |
| **Task** | 100-150 words | The specific challenge they faced | Create empathy |
| **Action** | 200-300 words | What solution was implemented, how | Show your product |
| **Result** | 100-200 words | Measurable outcomes, before/after | Prove value |

**Total: 800-1200 words.** Longer loses readers. Shorter lacks credibility.

## Structure Template

### 1. Headline (Lead with the Result)

```
❌ "How Company X Uses Our Product"
❌ "Company X Case Study"

✅ "How Company X Reduced Onboarding Time by 60% with [Product]"
✅ "Company X Grew Revenue 340% in 6 Months Using [Product]"
```

The headline should be specific, quantified, and state the outcome.

### 2. Snapshot Box

Place at the top for skimmers:

```
┌─────────────────────────────────────┐
│ Company: Acme Corp                  │
│ Industry: E-commerce                │
│ Size: 200 employees                 │
│ Challenge: Manual order processing  │
│ Result: 60% faster fulfillment      │
│ Product: [Your Product]             │
└─────────────────────────────────────┘
```

### 3. Situation

- Who is the customer (industry, size, location)
- What relevant context existed before the problem
- 1-2 sentences of company background

### 4. Task / Challenge

- **Quantify the pain:** "spending 40 hours/week on manual data entry" not "had data problems"
- **Show stakes:** what would happen if unsolved (lost revenue, churn, missed deadlines)
- Include a customer quote about the frustration

### 5. Action / Solution

- What was implemented (your product/service)
- Timeline: "deployed in 2 weeks" / "3-month rollout"
- Key decisions or configurations
- Why they chose you over alternatives (briefly)
- 2-3 specific features that addressed the challenge

### 6. Results

- **Before/after metrics** — always quantified
- **Timeframe** — "within 3 months" / "in the first quarter"
- Unexpected benefits beyond the original goal
- Customer quote about the outcome

## Metrics That Matter

### How to Present Numbers

```
❌ "Improved efficiency"
❌ "Saved time"
❌ "Better results"

✅ "Reduced processing time from 4 hours to 45 minutes (81% decrease)"
✅ "Increased conversion rate from 2.1% to 5.8% (176% improvement)"
✅ "Saved $240,000 annually in operational costs"
```

### Metric Categories

| Category | Examples |
|----------|---------|
| **Time** | Hours saved, time-to-completion, deployment speed |
| **Money** | Revenue increase, cost reduction, ROI |
| **Efficiency** | Throughput, error rate, automation rate |
| **Growth** | Users gained, market expansion, feature adoption |
| **Satisfaction** | NPS change, retention rate, support tickets reduced |

### Data Visualization

```bash
# Generate a before/after comparison chart
infsh app run infsh/python-executor --input '{
  "code": "import matplotlib.pyplot as plt\nimport matplotlib\nmatplotlib.use(\"Agg\")\n\ncategories = [\"Processing Time\", \"Error Rate\", \"Cost per Order\"]\nbefore = [4, 12, 8.50]\nafter = [0.75, 1.5, 2.10]\n\nfig, ax = plt.subplots(figsize=(10, 6))\nx = range(len(categories))\nwidth = 0.35\nax.bar([i - width/2 for i in x], before, width, label=\"Before\", color=\"#ef4444\")\nax.bar([i + width/2 for i in x], after, width, label=\"After\", color=\"#22c55e\")\nax.set_ylabel(\"Value\")\nax.set_xticks(x)\nax.set_xticklabels(categories)\nax.legend()\nax.set_title(\"Impact of Implementation\")\nplt.tight_layout()\nplt.savefig(\"results-chart.png\", dpi=150)\nprint(\"Chart saved\")"
}'
```

## Customer Quotes

### What Makes a Good Quote

```
❌ "We love the product." (vague, could be about anything)
❌ "It's great." (meaningless)

✅ "We went from processing 50 orders a day to 200, without adding a single person to the team."
   — Sarah Chen, VP Operations, Acme Corp

✅ "Before [Product], our team dreaded Monday mornings because of the report backlog.
    Now it's automated and they can focus on actual analysis."
   — Marcus Rodriguez, Head of Analytics, DataCo
```

### Quote Placement

- **1 quote in the Challenge section** — about the frustration/pain
- **1-2 quotes in the Results section** — about the outcome/transformation
- Always attribute: full name, title, company

### Quote Formatting

```markdown
> "We went from processing 50 orders a day to 200, without adding anyone to the team."
>
> — Sarah Chen, VP Operations, Acme Corp
```

## Research Support

### Finding Industry Context

```bash
# Industry benchmarks
infsh app run tavily/search-assistant --input '{
  "query": "average e-commerce order processing time industry benchmark 2024"
}'

# Competitor landscape
infsh app run exa/search --input '{
  "query": "order management automation solutions market overview"
}'

# Supporting statistics
infsh app run exa/answer --input '{
  "question": "What percentage of e-commerce businesses still use manual order processing?"
}'
```

## Distribution Formats

| Format | Where | Notes |
|--------|-------|-------|
| **Web page** | /customers/ or /case-studies/ | Full version, SEO-optimized |
| **PDF** | Sales team, email attachment | Designed, downloadable, gated optional |
| **Slide deck** | Sales calls, presentations | 5-8 slides, visual-heavy |
| **One-pager** | Trade shows, quick reference | Snapshot + key metrics + quote |
| **Social post** | LinkedIn, Twitter | Key stat + quote + link to full |
| **Video** | Website, YouTube | Customer interview or animated |

### Social Media Snippet

```
Headline stat + brief context + customer quote + CTA

Example:
"60% faster order processing.

Acme Corp was drowning in manual fulfillment. 4 hours per batch. 12% error rate.

After implementing [Product]: 45 minutes per batch. 1.5% errors.

'We went from 50 orders a day to 200 without adding headcount.' — Sarah Chen, VP Ops

Read the full story → [link]"
```

## Writing Checklist

- [ ] Headline leads with the quantified result
- [ ] Snapshot box with company, industry, challenge, result at top
- [ ] Challenge is quantified, not vague
- [ ] 2-3 specific customer quotes with attribution
- [ ] Before/after metrics with timeframe
- [ ] 800-1200 words total
- [ ] Skimmable (headers, bold, bullet points)
- [ ] Customer approved the final version
- [ ] Visual: at least one chart or before/after comparison

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| No specific numbers | Reads like marketing fluff | Quantify everything |
| All about your product | Reads like a sales pitch | Story is about the CUSTOMER |
| Generic quotes | No credibility | Get specific, attributed quotes |
| Missing the "before" | No contrast to show impact | Always show the starting point |
| Too long | Loses reader attention | 800-1200 words max |
| No customer approval | Legal/relationship risk | Always get sign-off |

## Related Skills

```bash
npx skills add inference-sh/skills@web-search
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
