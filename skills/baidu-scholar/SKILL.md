---
name: openakita/skills@baidu-scholar
description: "Baidu Scholar academic search skill. Search academic papers, journals, and knowledge resources. Use when user needs academic literature, research papers, or scholarly information."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
requires:
  env: [BAIDU_API_KEY]
---

# Baidu Scholar

Provides academic literature and knowledge discovery capabilities, enabling agents to dive into research, education, and other academic verticals.

## Configuration

export BAIDU_API_KEY="your_key"

## Features

- Academic paper search
- Journal literature retrieval
- Knowledge graph queries
- Citation relationship analysis

## Pre-built Scripts

### scripts/baidu_scholar.py
Academic paper search (Baidu Qianfan AppBuilder). Requires APPBUILDER_TOKEN to be set.

```bash
python3 scripts/baidu_scholar.py search "transformer attention mechanism"
python3 scripts/baidu_scholar.py cite "attention is all you need"
```
