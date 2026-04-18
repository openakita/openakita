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

为智能体注入权威、实时、结构化的中文百科知识，确保其回答的准确性与可信度。

## Configuration

export BAIDU_API_KEY="your_key"

## 使用

输入名词或概念，返回百度百科的标准化详细解释。依赖 Python 3 和 requests 库。

## Pre-built Scripts

### scripts/baidu_baike.py
百度百科Entry query脚本。

```bash
python3 scripts/baidu_baike.py search "量子计算"
```
