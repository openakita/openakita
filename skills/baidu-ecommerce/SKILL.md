---
name: openakita/skills@baidu-ecommerce
description: "Baidu E-commerce skill for cross-platform price comparison, review analysis, and purchase knowledge. Complete workflow from product discovery to purchase decision. Use when user wants to compare prices, read reviews, or make purchase decisions."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# Baidu E-commerce

赋予智能体跨平台比价、口碑Analyze、选购知识等结构化能力，一站式完成从找货到决策到下单的全流程电商任务。

## Features

- 跨平台商品比价
- 用户口碑与评价Analyze
- 选购知识与Recommendations
- 从找货到下单的Full链路

## Pre-built Scripts

### scripts/ecommerce.py
商品比价/口碑Analyze（百度千帆 AppBuilder），需Set APPBUILDER_TOKEN。

```bash
python3 scripts/ecommerce.py compare "iPhone 16 Pro"
python3 scripts/ecommerce.py review "戴森吹风机"
python3 scripts/ecommerce.py recommend "降噪耳机"
```
