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

# 智能 PPT Generation

根据主题与大纲，QuickGeneration结构清晰、排版专业的演示文稿草稿，大幅提升内容生产力。

## Configuration

export BAIDU_API_KEY="your_key"

## Features

- Based on主题AutomaticGeneration大纲
- 专业排版与布局
- 多模板风格Supports
- 内容结构优化

## Pre-built Scripts

### scripts/ppt_gen.py
PPT 大纲Generation（百度千帆 AppBuilder），需Set APPBUILDER_TOKEN。

```bash
python3 scripts/ppt_gen.py generate "Q2季度销售报告"
```
