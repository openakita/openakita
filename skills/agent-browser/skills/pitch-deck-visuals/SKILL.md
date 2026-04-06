---
name: pitch-deck-visuals
description: "Investor pitch deck structure with slide-by-slide framework, visual design rules, and data presentation. Covers the 12-slide framework, chart types, team slides, and common investor turn-offs. Use for: fundraising decks, investor presentations, startup pitch, demo day, grant proposals. Triggers: pitch deck, investor deck, startup pitch, fundraising deck, demo day, pitch presentation, investor presentation, seed deck, series a deck, pitch slides, startup presentation, vc pitch, investor meeting"
allowed-tools: Bash(infsh *)
---

# Pitch Deck Visuals

Create investor-ready pitch deck visuals via [inference.sh](https://inference.sh) CLI.

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate a slide background
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1920px;height:1080px;background:linear-gradient(135deg,#0f0f23,#1a1a3e);display:flex;align-items:center;padding:100px;font-family:system-ui;color:white\"><div><p style=\"font-size:24px;color:#818cf8;text-transform:uppercase;letter-spacing:3px\">The Problem</p><h1 style=\"font-size:72px;margin:16px 0;font-weight:800;line-height:1.1\">Teams waste 12 hours/week on manual reporting</h1><p style=\"font-size:28px;opacity:0.7\">Source: Forrester Research, 2024</p></div></div>"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## The 12-Slide Framework

| # | Slide | Duration | Content |
|---|-------|----------|---------|
| 1 | **Title** | 15s | Company name, tagline, your name |
| 2 | **Problem** | 45s | Pain point with data |
| 3 | **Solution** | 45s | Your product in one sentence |
| 4 | **Demo/Product** | 60s | Screenshot or live demo |
| 5 | **Market Size** | 30s | TAM → SAM → SOM |
| 6 | **Business Model** | 30s | How you make money |
| 7 | **Traction** | 45s | Growth metrics, customers |
| 8 | **Competition** | 30s | Positioning, not feature list |
| 9 | **Team** | 30s | Why you specifically will win |
| 10 | **Financials** | 30s | Revenue projections, unit economics |
| 11 | **The Ask** | 15s | How much, what for |
| 12 | **Contact** | 10s | Email, next steps |

**Total: ~6 minutes.** Never exceed 20 slides.

## Slide Design Rules

### Typography

| Element | Size (1920x1080) | Rule |
|---------|-----------------|------|
| Slide title | 48-72px | Max 6 words |
| Key stat/number | 96-144px | One per slide, when applicable |
| Body text | 24-32px | Max 6 bullet points |
| Caption/source | 16-20px | Cite data sources |
| Font | Sans-serif only | Inter, Helvetica, SF Pro, or similar |

### The 1-6-6 Rule

- **1** idea per slide
- **6** words max per bullet
- **6** bullets max per slide

If you need more text, you need more slides.

### Color

| Element | Guideline |
|---------|-----------|
| Background | Dark (navy, charcoal) OR clean white — pick one, commit |
| Accent color | ONE brand color for emphasis |
| Text | White on dark, or dark grey (#333) on light |
| Charts | 2-3 colors max, your brand color = "you" |
| Avoid | Gradients on text, neon colors, more than 3 colors |

### Layout

| Rule | Why |
|------|-----|
| Consistent margins (80-100px) | Professional, clean |
| Left-align text (never center body text) | Easier to scan |
| One visual per slide | Focus attention |
| Slide numbers | Helps investors reference specific slides |
| Logo in corner | Subtle brand reinforcement |

## Slide-by-Slide Visual Guide

### 1. Title Slide

```bash
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1920px;height:1080px;background:#0f0f23;display:flex;align-items:center;justify-content:center;font-family:system-ui;color:white;text-align:center\"><div><h1 style=\"font-size:80px;font-weight:900;margin:0\">DataFlow</h1><p style=\"font-size:32px;opacity:0.7;margin-top:16px\">Automated reporting for data teams</p><p style=\"font-size:22px;opacity:0.5;margin-top:40px\">Seed Round — Q1 2025</p></div></div>"
}'
```

### 2. Problem Slide

**One big number + one sentence.**

```bash
infsh app run infsh/html-to-image --input '{
  "html": "<div style=\"width:1920px;height:1080px;background:#0f0f23;display:flex;align-items:center;padding:100px;font-family:system-ui;color:white\"><div><p style=\"font-size:24px;color:#f59e0b;text-transform:uppercase;letter-spacing:3px;margin:0\">The Problem</p><h1 style=\"font-size:144px;margin:20px 0;font-weight:900;color:#f59e0b\">12 hrs/week</h1><p style=\"font-size:36px;opacity:0.8;line-height:1.4\">The average data analyst spends 12 hours per week<br>building reports manually</p><p style=\"font-size:20px;opacity:0.4;margin-top:30px\">Source: Forrester Research, 2024</p></div></div>"
}'
```

### 5. Market Size (TAM/SAM/SOM)

Use concentric circles, not pie charts:

```bash
infsh app run infsh/python-executor --input '{
  "code": "import matplotlib.pyplot as plt\nimport matplotlib\nmatplotlib.use(\"Agg\")\n\nfig, ax = plt.subplots(figsize=(19.2, 10.8))\nfig.patch.set_facecolor(\"#0f0f23\")\nax.set_facecolor(\"#0f0f23\")\n\ncircles = [\n    (0, 0, 4.0, \"#1e1e4a\", \"TAM\\n$50B\", 40),\n    (0, 0, 2.8, \"#2a2a5a\", \"SAM\\n$8B\", 32),\n    (0, 0, 1.4, \"#818cf8\", \"SOM\\n$800M\", 28)\n]\n\nfor x, y, r, color, label, fontsize in circles:\n    circle = plt.Circle((x, y), r, color=color, ec=\"#333366\", linewidth=2)\n    ax.add_patch(circle)\n    ax.text(x, y, label, ha=\"center\", va=\"center\", fontsize=fontsize, color=\"white\", fontweight=\"bold\")\n\nax.set_xlim(-5, 5)\nax.set_ylim(-5, 5)\nax.set_aspect(\"equal\")\nax.axis(\"off\")\nax.text(0, 4.8, \"Market Opportunity\", ha=\"center\", fontsize=36, color=\"white\", fontweight=\"bold\")\nplt.tight_layout()\nplt.savefig(\"market-size.png\", dpi=100, facecolor=\"#0f0f23\")\nprint(\"Saved\")"
}'
```

### 7. Traction Slide

**Show growth, not just numbers.** Up-and-to-the-right chart.

```bash
infsh app run infsh/python-executor --input '{
  "code": "import matplotlib.pyplot as plt\nimport matplotlib\nmatplotlib.use(\"Agg\")\n\nfig, ax = plt.subplots(figsize=(19.2, 10.8))\nfig.patch.set_facecolor(\"#0f0f23\")\nax.set_facecolor(\"#0f0f23\")\n\nmonths = [\"Jan\", \"Feb\", \"Mar\", \"Apr\", \"May\", \"Jun\", \"Jul\", \"Aug\"]\nrevenue = [8, 12, 18, 28, 42, 58, 82, 120]\n\nax.fill_between(range(len(months)), revenue, alpha=0.3, color=\"#818cf8\")\nax.plot(range(len(months)), revenue, color=\"#818cf8\", linewidth=4, marker=\"o\", markersize=10)\nax.set_xticks(range(len(months)))\nax.set_xticklabels(months, color=\"white\", fontsize=18)\nax.tick_params(colors=\"white\", labelsize=16)\nax.set_ylabel(\"MRR ($K)\", color=\"white\", fontsize=20)\nax.spines[\"top\"].set_visible(False)\nax.spines[\"right\"].set_visible(False)\nax.spines[\"bottom\"].set_color(\"#333\")\nax.spines[\"left\"].set_color(\"#333\")\nax.set_title(\"Monthly Recurring Revenue\", color=\"white\", fontsize=32, fontweight=\"bold\", pad=20)\nax.text(7, 120, \"$120K MRR\", color=\"#22c55e\", fontsize=28, fontweight=\"bold\", ha=\"center\", va=\"bottom\")\nax.text(7, 112, \"15x growth in 8 months\", color=\"#22c55e\", fontsize=18, ha=\"center\")\nplt.tight_layout()\nplt.savefig(\"traction.png\", dpi=100, facecolor=\"#0f0f23\")\nprint(\"Saved\")"
}'
```

### 8. Competition Slide

**Never use a feature matrix against competitors.** Use a 2x2 positioning map.

```bash
# See the competitor-teardown skill for positioning map generation
infsh app run infsh/python-executor --input '{
  "code": "import matplotlib.pyplot as plt\nimport matplotlib\nmatplotlib.use(\"Agg\")\n\nfig, ax = plt.subplots(figsize=(19.2, 10.8))\nfig.patch.set_facecolor(\"#0f0f23\")\nax.set_facecolor(\"#0f0f23\")\n\ncompetitors = {\n    \"Us\": (0.6, 0.7, \"#22c55e\", 300),\n    \"Legacy Tool\": (-0.5, 0.5, \"#6366f1\", 200),\n    \"Startup X\": (0.3, -0.4, \"#6366f1\", 200),\n    \"Manual Process\": (-0.6, -0.6, \"#475569\", 150)\n}\n\nfor name, (x, y, color, size) in competitors.items():\n    ax.scatter(x, y, s=size*5, c=color, zorder=5, alpha=0.8)\n    weight = \"bold\" if name == \"Us\" else \"normal\"\n    ax.annotate(name, (x, y), textcoords=\"offset points\", xytext=(15, 15), fontsize=22, color=\"white\", fontweight=weight)\n\nax.axhline(y=0, color=\"#333\", linewidth=1)\nax.axvline(x=0, color=\"#333\", linewidth=1)\nax.set_xlim(-1, 1)\nax.set_ylim(-1, 1)\nax.set_xlabel(\"Manual ← → Automated\", fontsize=22, color=\"white\", labelpad=15)\nax.set_ylabel(\"Basic ← → Advanced\", fontsize=22, color=\"white\", labelpad=15)\nax.set_title(\"Competitive Landscape\", fontsize=32, color=\"white\", fontweight=\"bold\", pad=20)\nax.tick_params(colors=\"#0f0f23\")\nfor spine in ax.spines.values():\n    spine.set_visible(False)\nplt.tight_layout()\nplt.savefig(\"competition.png\", dpi=100, facecolor=\"#0f0f23\")\nprint(\"Saved\")"
}'
```

### 9. Team Slide

```bash
# Generate professional team headshots/avatars
infsh app run falai/flux-dev-lora --input '{
  "prompt": "professional headshot portrait, person in business casual attire, clean neutral background, warm studio lighting, confident friendly expression, corporate photography style",
  "width": 512,
  "height": 512
}'
```

Layout: Photos in a row with name, title, and one credential each.

| Person | Format |
|--------|--------|
| CEO | Name, title, "Ex-[Company], [credential]" |
| CTO | Name, title, "Built [thing] at [Company]" |
| Others | Name, title, one relevant credential |

**Max 4 people on the team slide.** More = unfocused.

## Chart Guidelines

| Chart Type | Use For | Never Use For |
|-----------|---------|--------------|
| Line chart | Growth over time (traction) | Comparisons between categories |
| Bar chart | Comparing amounts | Time series (use line) |
| Concentric circles | TAM/SAM/SOM | Anything else |
| 2x2 matrix | Competitive positioning | Feature comparison |
| Single big number | Key metric highlight | Multiple metrics |
| Pie chart | NEVER | Anything (hard to read, unprofessional) |

### Chart Design Rules

| Rule | Why |
|------|-----|
| Max 2 colors per chart | Clarity |
| Your company = green or brand color | Positive association |
| Label directly on chart | No separate legend needed |
| Remove gridlines or make very subtle | Reduce clutter |
| Start Y-axis at 0 | Don't mislead |
| Cite data sources | Credibility |

## What Investors Look For

| Slide | Investor's Real Question |
|-------|------------------------|
| Problem | "Is this a real problem people pay to solve?" |
| Solution | "Is this 10x better than the status quo?" |
| Market | "Is this big enough to matter?" |
| Traction | "Is this actually working?" |
| Team | "Can these people execute?" |
| Ask | "Is this a reasonable deal?" |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Too many slides (20+) | Loses attention, unfocused | Max 12-15 slides |
| Wall of text | Nobody reads it | 1-6-6 rule: 1 idea, 6 words, 6 bullets |
| Feature comparison table vs competitors | Looks defensive | Use 2x2 positioning map |
| Pie charts | Hard to read, unprofessional | Use bar charts or big numbers |
| No data sources cited | Looks made up | Always cite sources |
| Team slide with 8+ people | Unfocused | Max 4, focus on relevant experience |
| Inconsistent design | Looks amateur | Same colors, fonts, margins on every slide |
| No "The Ask" slide | Investor doesn't know what you want | State amount, use of funds, timeline |
| Vanity metrics | "1M visits" means nothing without conversion | Show revenue, active users, retention |
| Too much product demo | This is a business pitch, not a demo | Max 2 slides on product, focus on business |

## Related Skills

```bash
npx skills add inference-sh/skills@competitor-teardown
npx skills add inference-sh/skills@data-visualization
npx skills add inference-sh/skills@ai-image-generation
```

Browse all apps: `infsh app list`
