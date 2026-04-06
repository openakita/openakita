---
name: openakita/skills@baidu-scholar
description: "Baidu Scholar academic search skill. Search academic papers, journals, and knowledge resources. Use when user needs academic literature, research papers, or scholarly information."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
requires:
  env: [BAIDU_API_KEY]
---

# 百度学术检索

提供专业的学术文献与知识检索能力，助力智能体深入科研、教育等垂直领域。

## 配置

export BAIDU_API_KEY="your_key"

## 功能

- 学术论文搜索
- 期刊文献检索
- 知识图谱查询
- 引用关系分析

## 预置脚本

### scripts/baidu_scholar.py
学术论文搜索（百度千帆 AppBuilder），需设置 APPBUILDER_TOKEN。

```bash
python3 scripts/baidu_scholar.py search "transformer attention mechanism"
python3 scripts/baidu_scholar.py cite "attention is all you need"
```
