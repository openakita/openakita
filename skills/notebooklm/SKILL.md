---
name: openakita/skills@notebooklm
description: Conduct deep research using NotebookLM integration — upload documents, query with citation-backed answers, synthesize findings, and produce infographic-style presentations. Output in Markdown, HTML/reveal.js slides, or Mermaid diagrams with visual hierarchy design specifications.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
  upstream: alfredang/skills/notebooklm
---

# NotebookLM Research Skill

Leverage NotebookLM's deep research capabilities to extract knowledge from uploaded documents, generate citation-backed answers, and transform findings into high-quality infographic-style presentations and structured output.

---

## Core Capabilities

1. **Deep Research** — Based on uploaded documents, conduct multi-round deep Q&A with citation-traceable answers
2. **Infographic Generation** — Transform research findings into visually structured presentations
3. **Knowledge Synthesis** — Cross-reference and synthesize insights from multiple sources
4. **Multi-Format Output** — Supports Markdown, HTML/reveal.js, and Mermaid diagram output
5. **Comparative Analysis** — Analyze documents to identify similarities, differences, and trends

---

## Workflow

### Full Pipeline

```
Upload → Research → Query → Analyze → Synthesize → Output
   │          │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼          ▼
 Sources   Deep Q&A   Citation   MECE     Insights   Visual
```

### Phase 1: Upload and Document Setup

#### Supported Source Types

| Format | Content | Notes |
|--------|---------|-------|
| PDF | Academic papers, reports | Best quality (OCR) |
| Web URLs | Articles, blog posts | Full page content |
| Text files | TXT, Markdown | Plain text |
| Google Docs | In Drive | Auto-sync |
| Google Slides | Presentation slides | Extracts text |
| YouTube Videos | Educational content | Has transcript / No transcript |
| Audio files | MP3, WAV | Has transcription |

#### Documentation Steps

1. **Upload sources** — Upload and register documents in Notebook
2. **Verify extraction** — Confirm full text extraction, check for errors
3. **Create study guide** — Organize Notebook with 5-50 sources
4. **Group sources** — By topic, date, or research perspective
5. **Record source metadata** — Author, date, publisher, type

#### Source Management

```markdown
## Sources

| # | Title | Type | Author / Publisher | Date | Status |
|---|-------|------|-------------------|------|--------|
| 1 | Title | PDF/URL/... | Author | 2024 | Active |
```

### Phase 2: Query and Research

#### Query Types

**Three research depths:**
- Shallow — Quick fact verification
- Medium — Multi-source synthesis via uploaded documents
- Deep — Full analysis with comprehensive comparison

**Query types and examples:**

| Type | Description | Example |
|---------|------|------|
| **Fact extraction** | Extract specific data or claims | "What is the market share of [product]?" |
| **Comparison** | Compare concepts, approaches, or results | "What are the differences between Method A and Method B in the literature?" |
| **Trend analysis** | Analyze changes over time | "Based on the literature, what is the trend of [topic]?" |
| **Definition** | Clarify concepts and terms | "What exactly is [concept]? How is it defined in different sources?" |
| **Temporal research** | Track changes across a time period | "From 2020 to 2025, how has the [field] evolved?" |
| **Gap identification** | Find what is missing | "What aspects have not been covered in the sources?" |

#### Deep Research Dialogue

```
Round 1 (Broad):
"What are the main findings in [topic] in these sources?"
       │
       ▼
Round 2 (Deepening):
"In [specific area], what methodologies and evidence are discussed?"
       │
       ▼
Round 3 (Challenge):
"Which sources contradict or do not support [this claim]?"
       │
       ▼
Round 4 (Synthesis):
"Based on all discussions, summarize 3 key insights and remaining questions."
```

### Phase 3: Analysis and Synthesis

#### MECE Analysis Framework

**MECE Analysis (Mutually Exclusive, Collectively Exhaustive):**

```markdown
## Analysis: [Topic]

### Dimension 1: [Category Name]
- 1.1 [Sub-point]: Doc A, p.12
- 1.2 [Sub-point]: Doc B, §3

### Dimension 2: [Category Name]
- 2.1 [Sub-point]: Doc C, Fig.3
- 2.2 [Sub-point]: Doc A, p.45

### Cross-Source Insights
- Points A and C converge on [finding]
- Points B and A diverge on [aspect]

### Missing Info
- [Aspect] is not covered by any source
- [Methodology] needs further research
```

#### Citation Standards

When citing, follow these formats:

**Footnote style:**
```markdown
In the past 30 days, [product] grew 23.5%[^1], with a user retention rate of 35%[^2].

[^1]: "2025 Mobile App Report", App Annie, p.18
[^2]: "SaaS Metrics Benchmark", OpenView Partners, Table 3.2
```

**Inline citation:**
```markdown
> "Current LLMs have significant limitations in logical reasoning, requiring further research on interpretability."
> — Zhang et al., 2024, "Few-Shot Learning: A Survey", §4.3
```

**Summary table:**
```markdown
| # | Source | Publisher | Key Page |
|--------|------|------|---------|
| [^1] | App Annie | Industry Report | p.18 |
| [^2] | OpenView | Research Report | Table 3.2 |
```

### Phase 4: Output Generation

#### Output Format Selection

| Format | Best For | Features |
|--------|---------|------|
| **Markdown** | Reports, Wiki, academic papers | Citations, links |
| **HTML/reveal.js** | Presentations, pitches | Animation, slides |
| **Mermaid diagrams** | Flowcharts, mind maps, Gantt charts | Visual structure |
| **Hybrid Markdown** | Complex reports | Text + diagrams |

---

## Infographic Designer

### Visual Hierarchy

Follow visual hierarchy principles — the most important information should be the most prominent.

#### Information Levels

```
Level 0: Title — One line, states the core message
   ↓
Level 1: Key metrics / Core findings — Largest size, bold, accent color
   ↓
Level 2: Supporting data / Secondary insights — Medium size, subheadings
   ↓
Level 3: Explanations / Context — Text blocks, descriptions
   ↓
Level 4: Footnotes / References — Smallest size, auxiliary info
```

#### Colors

```markdown
### Color Palette Recommendations

**Palette A: Business/Data (trustworthy, professional)**
- Primary: #2563EB (blue)
- Accent: #F59E0B (amber)
- Success: #10B981 (green)
- Alert: #EF4444 (red)
- Background: #F8FAFC
- Text: #1E293B

**Palette B: Nature/Growth (fresh, positive)**
- Primary: #059669 (emerald)
- Accent: #7C3AED (purple)
- Secondary: #0891B2 (teal)
- Background: #F0FDF4
- Text: #1A2E05

**Palette C: Tech/Modern (cool, energetic)**
- Primary: #60A5FA (light blue)
- Accent: #FBBF24 (gold)
- Secondary: #34D399 (mint)
- Background: #0F172A
- Text: #E2E8F0
```

#### Typography

```markdown
### Typography (rem equivalent)

| Element | Size | Line Height | Weight |
|---------|------|------------|--------|
| Main title | 2.5rem (40px) | 1.2 | 800 |
| Slide title | 3.5rem (56px) | 1.0 | 900 |
| Section heading | 1.5rem (24px) | 1.3 | 700 |
| Body text | 1rem (16px) | 1.6 | 400 |
| Footnote / Note | 0.75rem (12px) | 1.4 | 300 |
```

### Info Layout Templates

#### Template 1: Report Style

```markdown
┌──────────────────────────────────────────┐
│ 📊 [Report Title]                         │
│                                          │
├──────────────────────────────────────────┤
│  ┌──────┐  ┌──────┐  ┌──────┐           │
│  │ 3.5x │  │ 78%  │  │ #1   │           │
│  │ Growth│  │Share │  │ Rank│           │
│  └──────┘  └──────┘  └──────┘           │
├──────────────────────────────────────────┤
│ Finding 1       │ Finding 2              │
│ ...             │ ...                    │
│ [Source A, B]   │ [Source C]             │
├──────────────────────────────────────────┤
│ 📈 Trend / Timeline                       │
├──────────────────────────────────────────┤
│ Comparison & Recommendations              │
│                                          │
└──────────────────────────────────────────┘
```

#### Template 2: Comparison Mode

```markdown
┌──────────────────────────────────────────┐
│ ⚖️ [Comparison Title]                     │
├───────────────────┬──────────────────────┤
│ Option A          │ Option B             │
├───────────────────┼──────────────────────┤
│ Point 1: ✅       │ Point 1: ⚠️          │
│ Point 2: ⚠️       │ Point 2: ✅          │
│ Point 3: ❌       │ Point 3: ✅          │
├───────────────────┴──────────────────────┤
│ Summary and Recommendations               │
│                                          │
└──────────────────────────────────────────┘
```

#### Template 3: Process/Timeline

```markdown
┌──────────────────────────────────────────┐
│ 🔄 [Process/Timeline Title]               │
├──────────────────────────────────────────┤
│  ①──────→②──────→③──────→④              │
│                                          │
│ Details:                                  │
│ Phase ①: [Description] [Source]          │
│ Phase ②: [Description] [Source]          │
├──────────────────────────────────────────┤
│ Key metrics at each stage                 │
└──────────────────────────────────────────┘
```

---

## Quality Standards

### Content Requirements
- Every claim must be traceable to at least one source
- Numbers, dates, and statistics must be cited
- Mark any uncertain information
- Clearly mark conflicting information from different sources

### Output Quality Checklist
- [ ] All data points have source citations
- [ ] Infographic follows the visual hierarchy levels
- [ ] Key metrics are highlighted at Level 1
- [ ] Color palette is consistent throughout
- [ ] Font sizes follow the typography guide
- [ ] Output can stand alone as a report/presentation

---

## EXTEND.md

Create `EXTEND.md` in the skill directory for:
- Detailed Mermaid syntax examples
- Reveal.js slide configuration
- Advanced citation formatting
- Multi-language source handling
