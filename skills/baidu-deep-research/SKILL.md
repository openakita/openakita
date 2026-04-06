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

百度千帆官方构建的复杂智能体应用范例，深度融合信息检索、多源分析、内容综合、报告生成。DeepResearch 排行榜第一。

## 配置

export BAIDU_API_KEY="your_key"

## 功能

- 信息检索：全网多源信息采集
- 多源分析：交叉验证与深度分析
- 内容综合：结构化内容整合
- 报告生成：专业研究报告输出

## 预置脚本

### scripts/deep_research.py
深度研究报告生成（百度千帆 AppBuilder），需设置 APPBUILDER_TOKEN。

```bash
python3 scripts/deep_research.py research "人工智能在医疗领域的应用"
python3 scripts/deep_research.py report "大模型技术趋势分析"
```
