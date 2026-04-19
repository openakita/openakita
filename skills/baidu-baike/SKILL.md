---
name: openakita/skills@baidu-baike
description: "Baidu Baike encyclopedia search skill. Provides authoritative, real-time, structured Chinese encyclopedia knowledge. Use when user needs factual information about concepts, people, places, or topics."
license: MIT
metadata:
  author: baidu
  version: "1.1.0"
requires:
  env: [BAIDU_API_KEY]
---

# Baidu Baike

Injects the agent with authoritative, real-time, structured Chinese encyclopedia knowledge, ensuring the accuracy and credibility of its answers.

## Configuration

export BAIDU_API_KEY="your_key"

## Usage

Enter a term or concept to return standardized, detailed explanations from Baidu Baike. Requires Python 3 and the requests library.

## Pre-built Scripts

### scripts/baidu_baike.py
Baidu Baike entry query script.

```bash
python3 scripts/baidu_baike.py search "quantum computing"
```
