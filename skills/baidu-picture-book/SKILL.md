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

# AI 绘本Generation

将简短的文字描述转化为连贯的绘本故事与画面构思，激发创意与视觉化表达。

## Configuration

export BAIDU_API_KEY="your_key"

## Features

- 文字转绘本故事
- 画面构思与描述
- 连续情节Generation
- 多风格Supports

## Pre-built Scripts

### scripts/picture_book.py
文字转绘本（百度千帆 AppBuilder），需Set APPBUILDER_TOKEN。

```bash
python3 scripts/picture_book.py generate "小兔子找妈妈的故事"
```
