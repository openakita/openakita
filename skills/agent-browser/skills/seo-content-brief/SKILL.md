---
name: seo-content-brief
description: "SEO content brief creation with keyword research, search intent analysis, and content structure. Covers SERP analysis, heading hierarchy, word count targets, and internal linking strategy. Use for: content briefs, SEO writing, blog strategy, content planning, keyword targeting. Triggers: seo content brief, content brief, seo brief, keyword research, search intent, content strategy, blog brief, seo writing, content planning, keyword targeting, serp analysis, content outline, seo article, blog seo"
allowed-tools: Bash(infsh *)
---

# SEO Content Brief

Create data-driven content briefs via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Research target keyword
infsh app run tavily/search-assistant --input '{
  "query": "best project management tools for small teams 2024"
}'

# Analyze top-ranking content
infsh app run exa/search --input '{
  "query": "project management tools small teams comparison guide"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Content Brief Template

Every brief should answer these questions before writing begins:

```markdown
# Content Brief: [Working Title]

## Target
- **Primary keyword:** [exact keyword]
- **Secondary keywords:** [3-5 related terms]
- **Search intent:** [informational / commercial / transactional / navigational]
- **Target word count:** [X,XXX words]
- **Target URL:** /blog/[slug]

## Search Intent Analysis
- What is the searcher trying to accomplish?
- What format do top results use? (listicle, guide, comparison, tutorial)
- What questions need answering?

## Outline
H1: [Title with primary keyword]
  H2: [Section 1]
    H3: [Subsection]
  H2: [Section 2]
  ...

## Competitors to Beat
1. [URL] — [word count] — [what they do well] — [gap/weakness]
2. [URL] — [word count] — [what they do well] — [gap/weakness]
3. [URL] — [word count] — [what they do well] — [gap/weakness]

## Unique Angle
What makes this piece different/better than what already ranks?

## Internal Links
- Link TO: [existing pages to link to from this article]
- Link FROM: [existing pages that should link to this new article]
```

## Search Intent Types

| Intent | What Searcher Wants | Content Format | Example Query |
|--------|-------------------|----------------|--------------|
| **Informational** | Learn something | Guide, tutorial, explainer | "what is CI/CD" |
| **Commercial** | Compare before buying | Comparison, listicle, review | "best CI/CD tools 2024" |
| **Transactional** | Buy/sign up | Product page, pricing page | "GitHub Actions pricing" |
| **Navigational** | Find a specific page | — (don't target these) | "GitHub login" |

**Match format to intent.** If top 10 results are all listicles, write a listicle. If they're all tutorials, write a tutorial. Fighting the SERP format loses.

## SERP Analysis Process

```bash
# Step 1: See what currently ranks
infsh app run tavily/search-assistant --input '{
  "query": "[your target keyword]"
}'

# Step 2: Analyze top-ranking content
infsh app run tavily/extract --input '{
  "urls": ["https://top-result-1.com/article", "https://top-result-2.com/article"]
}'

# Step 3: Find related questions (People Also Ask)
infsh app run tavily/search-assistant --input '{
  "query": "[keyword] questions people ask FAQ"
}'

# Step 4: Find content gaps
infsh app run exa/search --input '{
  "query": "[keyword] [subtopic competitors miss]"
}'
```

### What to Extract from Top Results

| Data Point | Why |
|-----------|-----|
| **Word count** | Sets your minimum (match or exceed top 3) |
| **Heading structure** | Shows what Google considers complete coverage |
| **Topics covered** | Every topic they cover, you must cover |
| **Topics missed** | Your opportunity to be more comprehensive |
| **Content format** | Listicle, guide, tutorial, comparison |
| **Media used** | Images, videos, tables, infographics |
| **Internal/external links** | Reference quality signals |

## Keyword Research

### Keyword Metrics

| Metric | What It Means | Target |
|--------|--------------|--------|
| **Search volume** | Monthly searches | Depends on niche (100+ for long-tail) |
| **Keyword difficulty** | Competition level | < 30 for new sites, < 50 for established |
| **CPC** | What advertisers pay | Higher CPC = more commercial value |
| **Search intent** | What users want | Must match your content type |

### Finding Keywords

```bash
# Seed keyword research
infsh app run tavily/search-assistant --input '{
  "query": "project management software long tail keywords related searches"
}'

# Find question-based keywords
infsh app run exa/search --input '{
  "query": "questions about project management tools for startups"
}'

# Competitor keyword analysis
infsh app run tavily/search-assistant --input '{
  "query": "site:competitor.com/blog top performing pages topics"
}'
```

### Keyword Clustering

Group related keywords into one piece of content:

```
Primary: "best project management tools for small teams"
Cluster:
  - "project management software small business"
  - "project management tools comparison"
  - "simple project management app"
  - "project management for startups"
  - "affordable project management software"
```

**One page per keyword cluster.** Don't create separate pages for each variation — that's keyword cannibalization.

## Heading Structure

### Rules

| Rule | Why |
|------|-----|
| One H1 per page | SEO standard, contains primary keyword |
| H2s = main sections | Each should target a secondary keyword or question |
| H3s = subsections | Break up long H2 sections |
| Primary keyword in H1 | Direct ranking signal |
| Secondary keywords in H2s | Topical coverage signal |
| Question format for some H2s | Targets "People Also Ask" |
| Logical hierarchy | Never skip levels (H1 → H3 without H2) |

### Example Structure

```
H1: Best Project Management Tools for Small Teams (2025)
  H2: How We Evaluated These Tools
  H2: Top 10 Project Management Tools Compared
    H3: 1. Tool A — Best for [use case]
    H3: 2. Tool B — Best for [use case]
    ...
  H2: Feature Comparison Table
  H2: How to Choose the Right Tool for Your Team
    H3: Team Size Considerations
    H3: Budget Considerations
  H2: Frequently Asked Questions
    H3: What is the easiest project management tool?
    H3: Do small teams need project management software?
  H2: Conclusion
```

## Word Count Targets

| Content Type | Word Count | When |
|-------------|-----------|------|
| Short-form blog | 800-1,200 | News, updates, opinions |
| Standard blog | 1,500-2,000 | How-tos, tutorials |
| Long-form guide | 2,500-4,000 | Comprehensive guides, comparisons |
| Pillar content | 4,000-7,000 | Definitive guides, hub pages |
| Glossary/definition | 300-800 | Quick reference terms |

**Rule: match or exceed the average word count of the top 3 ranking results.** Don't pad — every word should add value.

## On-Page SEO Checklist

| Element | Rule |
|---------|------|
| **Title tag** | Primary keyword + compelling hook, 50-60 characters |
| **Meta description** | Includes keyword, 150-160 characters, includes CTA |
| **URL slug** | Short, keyword-rich: `/best-project-management-tools` |
| **H1** | Primary keyword, matches search intent |
| **First 100 words** | Include primary keyword naturally |
| **Image alt text** | Descriptive, includes keyword where natural |
| **Internal links** | 3-5 links to related content |
| **External links** | 2-3 authoritative sources |
| **Schema markup** | FAQ, HowTo, or Article schema where applicable |

## Content Differentiation

### Unique Angles

| Angle | Example |
|-------|---------|
| **Original data** | "We surveyed 500 PMs — here's what they use" |
| **Expert quotes** | Interview practitioners for original insights |
| **Real examples** | Screenshots, case studies, not just descriptions |
| **More comprehensive** | Cover subtopics competitors skip |
| **More current** | Updated data, newer tools, recent changes |
| **Better visuals** | Comparison tables, infographics, decision trees |

```bash
# Generate comparison infographic
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1200px;height:800px;background:white;padding:40px;font-family:system-ui\"><h2 style=\"font-size:28px;color:#1e293b;text-align:center;margin-bottom:30px\">Project Management Tools Comparison</h2><table style=\"width:100%;border-collapse:collapse;font-size:16px\"><tr style=\"background:#f1f5f9\"><th style=\"padding:12px;text-align:left;border-bottom:2px solid #cbd5e1\">Feature</th><th style=\"padding:12px;text-align:center;border-bottom:2px solid #cbd5e1\">Tool A</th><th style=\"padding:12px;text-align:center;border-bottom:2px solid #cbd5e1\">Tool B</th><th style=\"padding:12px;text-align:center;border-bottom:2px solid #cbd5e1\">Tool C</th></tr><tr><td style=\"padding:12px;border-bottom:1px solid #e2e8f0\">Free tier</td><td style=\"padding:12px;text-align:center;border-bottom:1px solid #e2e8f0\">✅</td><td style=\"padding:12px;text-align:center;border-bottom:1px solid #e2e8f0\">✅</td><td style=\"padding:12px;text-align:center;border-bottom:1px solid #e2e8f0\">❌</td></tr></table></div>"
}'
```

## Internal Linking Strategy

| Type | Purpose |
|------|---------|
| **Hub → Spoke** | Pillar page links to all subtopic articles |
| **Spoke → Hub** | Subtopic articles link back to pillar page |
| **Spoke → Spoke** | Related articles cross-link to each other |
| **Contextual** | Natural in-text links where topic is mentioned |

### Rules

- 3-5 internal links minimum per article
- Anchor text should be descriptive (not "click here")
- Link to relevant pages, not random ones
- Update old articles to link to new ones
- Most important pages should have the most internal links

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| No SERP analysis | Writing blind, wrong format | Always analyze top 3-5 before writing |
| Wrong intent match | Guide when they want comparison, or vice versa | Match SERP format exactly |
| Keyword stuffing | Penalized, reads poorly | Use naturally, 1-2% density max |
| Ignoring "People Also Ask" | Missing easy ranking opportunities | Answer PAA questions in your content |
| Too short | Can't compete with comprehensive results | Match or exceed top 3 word count |
| No unique angle | Just another "me too" article | Original data, expert quotes, better visuals |
| Keyword cannibalization | Multiple pages competing | One page per keyword cluster |
| No internal links | Wasted link equity, poor site structure | 3-5 internal links per article |
| Missing meta description | Google generates one (often poorly) | Write compelling meta with keyword + CTA |

## Related Skills

```bash
npx skills add inference-sh/skills@seo
npx skills add inference-sh/skills@technical-blog-writing
npx skills add inference-sh/skills@web-search
```

Browse all apps: `infsh app list`
