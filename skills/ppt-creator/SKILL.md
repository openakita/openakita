---
name: openakita/skills@ppt-creator
description: Create professional presentations using the Pyramid Principle methodology. Supports PPTX generation, Marp/Reveal.js Markdown slides, chart creation, speaker notes, and self-evaluation rubrics. Minimal intake form to rapid output workflow.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# PPT Creator — Professional Presentation Generator

## When to Use

- When the user needs to create a professional presentation (PPT, deck, keynote, slides)
- Needs to generate a complete slide outline and visual design
- Needs generation including title, subtitle, body text, bullet points, images, charts
- Needs automatic generation of speaker notes
- Needs Markdown format output (Marp / Reveal.js)
- Has existing content that needs to be converted into a presentation

---

## Prerequisites

### Required

| Dependency | Purpose | Install |
|------|------|---------|
| Python ≥ 3.10 | Run generation scripts | |
| `python-pptx` | Generate PPTX files | `pip install python-pptx` |

### Optional

| Dependency | Purpose | Install |
|------|------|---------|
| `marp-cli` | Markdown → PPT/PDF | `npm install -g @marp-team/marp-cli` |
| `matplotlib` | Generate charts | `pip install matplotlib` |
| `Pillow` | Image processing | `pip install Pillow` |
| `plotly` | Interactive charts | `pip install plotly kaleido` |

### Validate Installation

```bash
python -c "from pptx import Presentation; print('python-pptx OK')"
marp --version
```

---

## Instructions

### Core Methodology: Pyramid Principle

Barbara Minto framework:

```
           ┌──────────────┐
           │ Main Conclusion │ ← 1 central idea
           └──────┬───────┘
        ┌─────────┼─────────┐
   ┌────┴────┐ ┌──┴───┐ ┌──┴────┐
│ Supporting A │ │ Supporting B │ │ Supporting C │ ← 3 key arguments
   └────┬────┘ └──┬───┘ └──┬────┘
   1-3 points   1-3 points   1-3 points ← Each with 2-3 sub-points
```

**Four Rules:**
1. **Conclusion First** — State the core message upfront
2. **Mutually Exclusive** — Points do not overlap or conflict
3. **Collectively Exhaustive** — Together they cover the full scope
4. **Logically Ordered** — Structured by time, importance, or degree

### Presentation Template Catalog

| Slide Type | Purpose | Duration |
|--------|------|------|
| Title | Audience hook, state the core message | — |
| Executive Summary | Core conclusion + key data | 30s |
| Context / Background | Problem and current situation | 45-60s |
| Slide 1 | Supporting argument 1 + data/chart | 45-60s |
| Slide 2 | Supporting argument 2 + data/chart | 45-60s |
| Slide 3 | Supporting argument 3 + data/chart | 45-60s |
| Comparison / Options | Pros and cons analysis | 45-60s |
| Timeline / Roadmap | Phased implementation plan | 45-60s |
| Budget / Resources | Numbers, requires clarity | 30s |
| Q&A / Discussion | Call to action and next steps | — |

---

## Workflows

### Workflow 1: Quick Create (Minimal Intake Form)

**Step 1 — Intake**

Ask 5 need questions (in table format):

| # | Question | Example Answer |
|---|------|---------|
| 1 | What is the presentation topic? | "Q4 Business Review" |
| 2 | Who is the audience? | "Management team" |
| 3 | What is the core conclusion / key takeaway? | "Revenue grew 30% but bottlenecks remain" |
| 4 | Do you have supporting data or case studies? | "Revenue up 200%, user analysis, competitor comparison" |
| 5 | How many slides / estimated duration? | "10-15 slides" |

Generate the outline and chart suggestions based on the answers.

**Step 2 — Generate Outline**

Based on the intake, produce a tree-style outline:

```
Topic: Revenue Growth 30%
├── A: Achievement Highlights (200% Revenue Growth)
│ ├── Key Metric 1: Growth Curve
│ ├── Key Metric 2: User Acquisition
│ └── Key Metric 3: Case Study
├── B: Challenges in Execution
│ ├── Challenge: Performance bottlenecks
│ ├── Challenge: Resource constraints
│ └── Response Strategy
└── C: Next Steps
├── Action Plan
├── Milestone 1: Bug fixes
└── Milestone 2: New feature launches
```

**Step 3 — Generate Slides**

Requirements:
- **Complete**: Full content ready to present (no placeholders)
- **Concise**: 3-5 key points per slide, text ≤ 150 words
- **Visual**: Include appropriate charts/data visualizations where needed
- **Paced**: Each slide supports ~45-60 seconds of speaking

**Step 4 — Self-Evaluation**

When the user requests quality review (use Output Format Partial).

---

### Workflow 2: Chart Generation

**Supported Chart Types**

| Chart Type | Use Case | Recommended Tool |
|------|---------|-----|
| Bar Chart | Comparison | matplotlib / plotly |
| Line Chart | Trend / growth | matplotlib / plotly |
| Pie / Donut | Proportion / distribution | matplotlib / plotly |
| Scatter Plot | Correlation analysis | matplotlib / plotly |
| Funnel Chart | Conversion analysis | plotly |
| Heat Map | Distribution patterns | matplotlib / seaborn |
| Stacked Bar | Composition breakdown | matplotlib |

**Chart Design Principles**

1. **One Chart, One Conclusion** — Clear title expressing the finding ("Q4 revenue up 40%" vs "Q4 Revenue")
2. **Highlight Key Data** — Use color to emphasize the focal point
3. **Simplify** — Remove gridlines, legends, and 3D effects
4. **Label Directly** — Place data labels on elements where possible
5. **Keep It Clean** — White space should occupy at least 60%

**Chart Generation Python Example**

```python
import matplotlib.pyplot as plt
import matplotlib

matplotlib.rcParams['font.sans-serif'] = ['Arial', 'Helvetica']
matplotlib.rcParams['axes.unicode_minus'] = False

fig, ax = plt.subplots(figsize=(10, 6))
categories = ['Q1', 'Q2', 'Q3', 'Q4']
values = [120, 180, 240, 350]
colors = ['#e0e0e0', '#e0e0e0', '#e0e0e0', '#4A90D9']

ax.bar(categories, values, color=colors, width=0.6)
ax.set_title('Q4 Revenue 350M, Up 46% YoY', fontsize=16, fontweight='bold', pad=20)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

for i, v in enumerate(values):
    ax.text(i, v + 5, f'{v}', ha='center', fontsize=12)

plt.tight_layout()
plt.savefig('chart_revenue.png', dpi=200, bbox_inches='tight')
```

---

### Workflow 3: Speaker Notes Generation

Generate 45-60 seconds of natural speech per slide:

**Principles:**
1. **Hook Opening**: Start with a question or relatable statement ("Have you ever wondered...", "Imagine a world where...")
2. **State the Point**: Explain what this slide is about and why it matters
3. **Elaborate Data**: Add narrative context to the figures
4. **Control Pace**: ~150-200 words ≈ 45-60 seconds of speech
5. **Add Transitions**: `[Transition to next slide]` markers between slides
6. **Include Cues**: `[Point to chart]`, `[Pause]` delivery cues

**Example Speaker Notes:**

```
Let's start with the most exciting number: our Q4 revenue hit an all-time high of 350 million. [Pause]

Over the past year, we grew from 50 million to 150 million — a 200% increase. This is not just a number; it represents every team's tireless effort.

Behind this growth, our user base expanded from 200 to over 800, covering 5 industry verticals. [Point to chart]

So what drove this? It was not luck — it was a deliberate strategy of doubling down on our core business while systematically expanding into new markets.
```

---

### Workflow 4: Presentation Self-Evaluation

After generation, evaluate using the rubric below:

| Dimension | Max Score | Evaluation Criteria |
|------|------|---------|
| Structure & Logic | 25 | Meets Pyramid Principle, complete and MECE |
| Visual Design | 20 | Clean, clear hierarchy, appropriate contrast |
| Data Accuracy | 20 | Correct charts, proper units, readable |
| Language Quality | 15 | Concise, professional, no typos |
| Speaker Notes | 10 | Natural, matches slide content |
| Call To Action | 10 | Clear CTA and next steps |

Passing score: ≥ 80/100. Auto-revise any slide scoring below 80.

---

## Output Format

### Format 1: PPTX (python-pptx)

Use `python-pptx` to generate PowerPoint files:

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

slide_layout = prs.slide_layouts[6]  # blank layout
slide = prs.slides.add_slide(slide_layout)

title_shape = slide.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(1.2))
tf = title_shape.text_frame
tf.text = "Q4 Revenue Up 46%, Record High"
tf.paragraphs[0].font.size = Pt(28)
tf.paragraphs[0].font.bold = True

notes_slide = slide.notes_slide
notes_slide.notes_text_frame.text = "..."

prs.save('presentation.pptx')
```

### Format 2: Marp Markdown

```markdown
---
marp: true
theme: default
paginate: true
header: "Business Review | Q4 Report"
footer: "Company Confidential"
---

# Q4 Business Review
### Revenue Grew 30%

---

## Key Achievements

- User count: 50 → 150 (+200%)
- Response time: 200ms → 800ms
- Launched 3 new product lines

![bg right:40%](chart_growth.png)

---
```

Usage:

```bash
marp slides.md --pptx -o presentation.pptx
marp slides.md --pdf -o presentation.pdf
marp slides.md --html -o presentation.html
```

### Format 3: Reveal.js HTML

```html
<!DOCTYPE html>
<html>
<head>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@4/dist/reveal.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@4/dist/theme/white.css">
</head>
<body>
<div class="reveal">
    <div class="slides">
        <section>
            <h1>Q4 Business Review</h1>
            <p>Revenue Grew 30%</p>
            <aside class="notes">Speaker notes here...</aside>
        </section>
        <section>
            <h2>Key Achievements</h2>
            <ul>
                <li>User count: 50 → 150 (+200%)</li>
                <li>Response time: 200ms → 800ms</li>
            </ul>
            <aside class="notes">Elaborate on these metrics...</aside>
        </section>
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@4/dist/reveal.js"></script>
<script>Reveal.initialize({ hash: true });</script>
</body>
</html>
```

### Format 4: PNG Export

Export individual slides as high-resolution PNG images, ≥ 200 DPI.

---

## Common Pitfalls

### 1. "Data Stacking" vs "Storytelling"

❌ **Bad**: "Q4 Revenue Report"
✅ **Good**: "Q4 Revenue Up 40%, Exceeding All Targets"

### 2. Over-Designing

Do not ask the user for 5 rounds of refinements if they just need a standard deck. The 5-question intake form is sufficient — do not expand to 15.

### 3. Charts Without Conclusions

A chart without a takeaway title is just decoration. Recommend:
- 1 clear conclusion per chart + 1 supporting data point
- Use color to draw attention to the key data (only 4 or fewer colors)

### 4. Data and Story Disconnect

Data must support the narrative. If a slide presents a problem, the next slide must address it with data.

### 5. Audience Mismatch

- Management: Focus on results, ROI, and strategy
- Technical team: Detail, implementation plan
- External / Press: Product highlights, social proof

### 6. Font Compatibility

When using python-pptx, specify fallback fonts:

```python
from pptx.util import Pt
paragraph.font.name = 'Calibri'
```

### 7. Underestimating Presentation Time

200-250 words per minute. 10 slides × 1 minute each = 10 minutes. For a 30-minute slot, plan for 25-30 slides or include Q&A buffer.

---

## EXTEND.md

Create `EXTEND.md` in the same directory for:
- Company PPT templates
- Color palette and font pairing recommendations
- PPT animation and transition guidelines
- Industry-specific content frameworks
