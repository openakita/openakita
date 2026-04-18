---
name: openakita/skills@baidu-marketing
description: "Baidu Intelligent Cloud marketing skill for generating marketing notes, videos, and optimized copy. Supports immediate and delayed call triggers. Use when user needs marketing content generation or campaign optimization."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# 百度智能云客悦营销

让 AI 更懂营销，覆盖笔记Generation、营销视频Generation、文案优化等场景，Supports立即呼叫与延迟呼叫。

## Features

- 营销笔记Generation
- 营销视频Generation
- 文案优化
- 呼叫触发（立即/延迟）

## Pre-built Scripts

### scripts/marketing.py
营销文案/方案Generation（百度千帆 AppBuilder），需Set APPBUILDER_TOKEN。

```bash
python3 scripts/marketing.py copywrite "新品咖啡上市推广"
python3 scripts/marketing.py plan "618大促营销方案"
```
