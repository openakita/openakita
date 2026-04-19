---
name: openakita/skills@baidu-search
description: "Baidu Web Search skill for real-time Chinese web information retrieval. Breaks through static knowledge base limitations to get the latest news and information. Use when user needs to search the Chinese web for current information."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
requires:
  env: [BAIDU_QIANFAN_AK, BAIDU_QIANFAN_SK]
---

# Baidu Search

Enables the agent to search the web in real time, breaking through static knowledge base limits to get the latest news and answers. The #1 downloaded search engine skill on ClawHub.

## Configuration

Apply for Baidu Qianfan API Key: https://console.bce.baidu.com/qianfan/ais/console/apikey

export BAIDU_QIANFAN_AK="your_ak"
export BAIDU_QIANFAN_SK="your_sk"

## Installation

clawhub install baidu-search --no-input

## Features

- Web search: real-time retrieval of web information
- Image search: similar image search via multimodal retrieval
- Time filtering: filter results by publication date
- Authority rating: results include relevance and authority scores

## Pre-built Scripts

### scripts/baidu_search.py
Baidu Search API wrapper. Requires BAIDU_QIANFAN_AK and BAIDU_QIANFAN_SK to be set.

```bash
python3 scripts/baidu_search.py web "Python async programming"
python3 scripts/baidu_search.py image "landscape wallpaper"
```
