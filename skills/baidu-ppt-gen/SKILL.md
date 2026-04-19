---
name: openakita/skills@baidu-ppt-gen
description: "AI PPT Generator skill. Quickly generate well-structured, professionally formatted presentation drafts from topics and outlines. Use when user wants to create PowerPoint presentations."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
requires:
  env: [BAIDU_API_KEY]
---

# Smart PPT Generation

Based on a topic and outline, quickly generate a well-structured, professionally formatted presentation draft to boost content productivity.

## Configuration

export BAIDU_API_KEY="your_key"

## Features

- Automatically generate outlines based on topic
- Professional layout and design
- Multiple template style support
- Content structure optimization

## Pre-built Scripts

### scripts/ppt_gen.py
PPT outline generation (Baidu Qianfan AppBuilder). Requires APPBUILDER_TOKEN.

```bash
python3 scripts/ppt_gen.py generate "Q2 Sales Report"
```
