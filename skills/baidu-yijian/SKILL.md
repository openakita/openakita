---
name: openakita/skills@baidu-yijian
description: "Baidu Yijian visual management skill for industrial scenarios. Provides visual recognition capabilities across 20+ industries including retail, energy, mining, ports, chemical, and steel. Use for industrial visual inspection and management."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# 百度一见

让 OpenClaw 具备视觉manage能力，覆盖零售餐饮、能源电力、矿山、港口、化工、钢铁等 20+ 行业。

## Features

- 工业视觉检测
- 多行业场景覆盖
- 实时监控与告警
- 视觉Analyze报告

## Pre-built Scripts

### scripts/yijian.py
工业视觉检测（百度千帆 AppBuilder），需Set APPBUILDER_TOKEN。

```bash
python3 scripts/yijian.py detect --image /path/to/product.jpg
python3 scripts/yijian.py report "产线质检Analyze"
```
