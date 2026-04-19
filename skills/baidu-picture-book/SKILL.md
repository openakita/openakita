---
name: openakita/skills@baidu-picture-book
description: "AI Picture Book generation skill. Transform short text descriptions into coherent picture book stories with visual compositions. Use when user wants to create illustrated stories or picture books from text."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
requires:
  env: [BAIDU_API_KEY]
---

# AI Picture Book Generator

Transforms short text descriptions into coherent picture book stories and visual scene concepts, inspiring creativity and visual expression.

## Configuration

export BAIDU_API_KEY="your_key"

## Features

- Text-to-picture-book story conversion
- Scene concept and visual description
- Continuous narrative generation
- Multi-style support

## Pre-built Scripts

### scripts/picture_book.py
Text to picture book generation (Baidu Qianfan AppBuilder). Requires setting APPBUILDER_TOKEN.

```bash
python3 scripts/picture_book.py generate "The story of a little rabbit looking for its mother"
```
