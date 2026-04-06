---
name: openakita/skills@baidu-ecommerce
description: "Baidu E-commerce skill for cross-platform price comparison, review analysis, and purchase knowledge. Complete workflow from product discovery to purchase decision. Use when user wants to compare prices, read reviews, or make purchase decisions."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# 百度电商

赋予智能体跨平台比价、口碑分析、选购知识等结构化能力，一站式完成从找货到决策到下单的全流程电商任务。

## 功能

- 跨平台商品比价
- 用户口碑与评价分析
- 选购知识与推荐
- 从找货到下单的完整链路

## 预置脚本

### scripts/ecommerce.py
商品比价/口碑分析（百度千帆 AppBuilder），需设置 APPBUILDER_TOKEN。

```bash
python3 scripts/ecommerce.py compare "iPhone 16 Pro"
python3 scripts/ecommerce.py review "戴森吹风机"
python3 scripts/ecommerce.py recommend "降噪耳机"
```
