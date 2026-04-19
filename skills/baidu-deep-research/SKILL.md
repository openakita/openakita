---
name: openakita/skills@baidu-deep-research
description: "Qianfan Deep Research Agent for complex research tasks. Combines information retrieval, multi-source analysis, content synthesis, and report generation. Use when user needs in-depth research, analysis reports, or comprehensive investigation on complex topics."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
requires:
  env: [BAIDU_API_KEY]
---

# Qianfan Deep Research Agent

A complex agent application built by Baidu Qianfan, deeply integrating information retrieval, multi-source analysis, content synthesis, and report generation. Ranked #1 on the DeepResearch leaderboard.

## Configuration

```bash
export BAIDU_API_KEY="your_key"
```

## Features

- **Information Retrieval**: Multi-source information gathering from across the web
- **Multi-source Analysis**: Cross-validation and in-depth analysis
- **Content Synthesis**: Structured content integration
- **Report Generation**: Professional research report output

## Pre-built Scripts

### scripts/deep_research.py
Deep research report generation (Baidu Qianfan AppBuilder). Requires APPBUILDER_TOKEN to be set.

```bash
python3 scripts/deep_research.py research "Applications of AI in healthcare"
python3 scripts/deep_research.py report "Analysis of large model technology trends"
```
