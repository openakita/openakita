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

# 智能 PPT 生成

根据主题与大纲，快速生成结构清晰、排版专业的演示文稿草稿，大幅提升内容生产力。

## 配置

export BAIDU_API_KEY="your_key"

## 功能

- 基于主题自动生成大纲
- 专业排版与布局
- 多模板风格支持
- 内容结构优化

## 预置脚本

### scripts/ppt_gen.py
PPT 大纲生成（百度千帆 AppBuilder），需设置 APPBUILDER_TOKEN。

```bash
python3 scripts/ppt_gen.py generate "Q2季度销售报告"
```
