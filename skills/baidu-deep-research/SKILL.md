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

# 千帆深度研究 Agent

百度千帆官方构建的复杂智能体应用范例，深度融合信息检索、多源Analyze、内容综合、报告Generation。DeepResearch 排行榜第一。

## Configuration

export BAIDU_API_KEY="your_key"

## Features

- 信息检索：全网多源信息采集
- 多源Analyze：交叉验证与深度Analyze
- 内容综合：结构化内容整合
- 报告Generation：专业研究报告输出

## Pre-built Scripts

### scripts/deep_research.py
深度研究报告Generation（百度千帆 AppBuilder），需Set APPBUILDER_TOKEN。

```bash
python3 scripts/deep_research.py research "人工智能在医疗领域的应用"
python3 scripts/deep_research.py report "大模型技术趋势Analyze"
```
