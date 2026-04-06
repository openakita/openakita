---
name: customer-persona
description: "Research-backed customer persona creation with market data and avatar generation. Covers demographics, psychographics, jobs-to-be-done, journey mapping, and anti-personas. Use for: marketing strategy, product development, UX research, sales enablement, content strategy. Triggers: customer persona, buyer persona, user persona, target audience, ideal customer, customer profile, audience research, user research, icp, ideal customer profile, target market, customer avatar, audience persona"
allowed-tools: Bash(infsh *)
---

# Customer Persona

Create data-backed customer personas with research and visuals via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Research your target market
infsh app run tavily/search-assistant --input '{
  "query": "SaaS product manager demographics pain points 2024 survey"
}'

# Generate a persona avatar
infsh app run falai/flux-dev-lora --input '{
  "prompt": "professional headshot photograph of a 35-year-old woman, product manager, friendly confident expression, modern office background, natural lighting, business casual attire, realistic portrait",
  "width": 1024,
  "height": 1024
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Persona Template

```
┌──────────────────────────────────────────────────────┐
│  [Avatar Photo]                                      │
│                                                      │
│  SARAH CHEN, 34                                      │
│  Product Manager at a Series B SaaS startup          │
│                                                      │
│  "I spend more time making reports than making       │
│   decisions."                                        │
│                                                      │
├──────────────────────────────────────────────────────┤
│  DEMOGRAPHICS          │  PSYCHOGRAPHICS             │
│  Age: 30-38            │  Values: efficiency, data   │
│  Income: $120-160K     │  Personality: analytical,   │
│  Education: BS/MBA     │    organized, collaborative │
│  Location: Urban US    │  Interests: productivity,   │
│  Role: Product/PM      │    leadership, AI tools     │
├──────────────────────────────────────────────────────┤
│  GOALS                 │  PAIN POINTS                │
│  • Ship features       │  • Too many meetings        │
│  faster                │  • Manual reporting (15     │
│  • Data-driven         │    hrs/week)                │
│  decisions             │  • Stakeholder alignment    │
│  • Team alignment      │    is slow                  │
│  • Career growth to    │  • Tool sprawl (8+ apps)   │
│    Director            │  • No single source of      │
│                        │    truth                    │
├──────────────────────────────────────────────────────┤
│  CHANNELS              │  BUYING TRIGGERS            │
│  • LinkedIn (daily)    │  • Peer recommendation      │
│  • Product Hunt        │  • Free trial experience    │
│  • Podcasts (commute)  │  • Integration with Jira    │
│  • Lenny's Newsletter  │  • Team plan pricing        │
│  • Twitter/X           │  • ROI calculator           │
└──────────────────────────────────────────────────────┘
```

## Building a Persona Step-by-Step

### Step 1: Research

Start with data, not assumptions.

```bash
# Market demographics
infsh app run tavily/search-assistant --input '{
  "query": "product manager salary demographics 2024 survey report"
}'

# Pain points and challenges
infsh app run exa/search --input '{
  "query": "biggest challenges facing product managers SaaS companies"
}'

# Tool usage patterns
infsh app run tavily/search-assistant --input '{
  "query": "most popular tools product managers use 2024 survey"
}'

# Content consumption habits
infsh app run exa/answer --input '{
  "question": "Where do product managers get their industry news and professional development?"
}'
```

### Step 2: Demographics

**Use ranges, not exact values.** Personas represent a segment, not one person.

| Field | Format | Example |
|-------|--------|---------|
| Age range | X-Y | 30-38 |
| Income range | $X-$Y | $120,000-$160,000 |
| Education | Common degrees | BS Computer Science, MBA |
| Location | Region/type | Urban US, major tech hubs |
| Job title | Role level | Senior PM, Product Lead |
| Company size | Range | 50-500 employees |
| Industry | Sector | B2B SaaS |

### Step 3: Psychographics

What they think, value, and believe.

| Category | Questions to Answer |
|----------|-------------------|
| **Values** | What matters most to them professionally? |
| **Attitudes** | How do they feel about their industry's direction? |
| **Motivations** | What drives them at work? |
| **Personality** | Analytical vs intuitive? Leader vs collaborator? |
| **Interests** | What do they read/watch/listen to professionally? |
| **Lifestyle** | Work-life balance preference? Remote/hybrid/office? |

### Step 4: Goals

What they're trying to achieve (both professional and personal).

```
Professional:
- Ship features faster with fewer meetings
- Make data-driven decisions (not gut feelings)
- Get promoted to Director of Product within 2 years
- Build a more autonomous product team

Personal:
- Leave work by 6pm more often
- Be seen as a strategic leader, not a ticket manager
- Stay current with industry trends without information overload
```

### Step 5: Pain Points

**Quantify whenever possible.** Vague pain = vague persona.

```
❌ "Has trouble with reporting"
✅ "Spends 15 hours per week creating manual reports for 4 different stakeholders"

❌ "Too many tools"
✅ "Uses 8 different tools daily (Jira, Slack, Notion, Figma, Analytics, Sheets, Docs, Email) with no unified view"

❌ "Meetings are a problem"
✅ "Averages 6 hours of meetings per day, leaving only 2 hours for deep work"
```

### Step 6: Jobs-to-be-Done (JTBD)

Three types of jobs:

| Job Type | Description | Example |
|----------|-------------|---------|
| **Functional** | The task they need to accomplish | "Prioritize the product backlog based on customer impact data" |
| **Emotional** | How they want to feel | "Feel confident presenting to the exec team" |
| **Social** | How they want to be perceived | "Be seen as the person who makes data-driven decisions" |

### Step 7: Buying Process

| Stage | Behavior |
|-------|----------|
| **Awareness** | Reads blog posts, sees peer recommendations on LinkedIn |
| **Consideration** | Compares 3-4 tools, reads G2/Capterra reviews, asks in Slack communities |
| **Decision** | Requests demo, needs IT/security approval, evaluates team pricing |
| **Influencers** | Engineering lead, VP of Product, CFO (for budget) |
| **Objections** | "Will my team actually adopt it?", "Does it integrate with Jira?" |
| **Trigger event** | New quarter with aggressive goals, new VP demanding better reporting |

### Step 8: Generate Avatar

```bash
# Match demographics: age, gender, ethnicity, professional context
infsh app run falai/flux-dev-lora --input '{
  "prompt": "professional headshot photograph of a 34-year-old Asian American woman, product manager, warm confident smile, modern tech office background, natural lighting, wearing smart casual blouse, realistic portrait photography, sharp focus",
  "width": 1024,
  "height": 1024
}'
```

**Avatar tips:**
- Match the age range, ethnicity representation, and professional context
- Use "professional headshot photograph" for realistic results
- Friendly, approachable expression (not stock-photo-stiff)
- Background suggests their work environment
- Business casual or industry-appropriate attire

## The Anti-Persona

Equally important: who is NOT your customer.

```
ANTI-PERSONA: "Enterprise Earl"
- CTO at a 5,000+ person enterprise
- Needs SOC 2, HIPAA, on-premise deployment
- 18-month procurement cycles
- Wants white-glove onboarding and dedicated CSM
- WHY NOT: Our product is self-serve SaaS for SMB/mid-market.
  Enterprise needs would require 2+ years of product investment.
```

Anti-personas prevent wasted effort on customers you can't serve.

## Multiple Personas

Most products have 2-4 personas. More than 4 = too many to serve well.

| Priority | Persona | Role |
|----------|---------|------|
| **Primary** | The main user and buyer | Who you optimize for |
| **Secondary** | Influences the buying decision | Who you need to convince |
| **Tertiary** | Uses the product occasionally | Who you support, not target |

## Validation

Personas based on assumptions are fiction. Validate with:

| Method | What You Learn |
|--------|---------------|
| Customer interviews (5-10) | Real language, real pain points |
| Support ticket analysis | Actual problems, not assumed ones |
| Analytics data | Actual behavior, not reported behavior |
| Survey (50+ responses) | Quantified patterns across segments |
| Sales call recordings | Objections, buying triggers, language |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Based on assumptions | Fiction, not research | Start with data |
| Too many personas (6+) | Can't serve everyone well | Max 3-4 |
| Vague pain points | Not actionable | Quantify everything |
| Demographics only | Misses motivations and behavior | Add psychographics, JTBD |
| Never updated | Becomes outdated | Review quarterly |
| No anti-persona | Wasted effort on wrong customers | Define who you're NOT for |
| Single persona for all | Different users have different needs | Primary/secondary/tertiary |

## Related Skills

```bash
npx skills add inference-sh/skills@web-search
npx skills add inference-sh/skills@ai-image-generation
npx skills add inference-sh/skills@prompt-engineering
```

Browse all apps: `infsh app list`
