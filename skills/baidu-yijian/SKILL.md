---
name: openakita/skills@baidu-yijian
description: "Baidu Yijian visual management skill for industrial scenarios. Provides visual recognition capabilities across 20+ industries including retail, energy, mining, ports, chemical, and steel. Use for industrial visual inspection and management."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# Baidu Yijian

Equips OpenClaw with visual management capabilities, covering 20+ industries including retail & food service, energy & power, mining, ports, chemicals, and steel.

## Features

- Industrial visual inspection
- Multi-industry scenario coverage
- Real-time monitoring and alerts
- Visual analysis reports

## Pre-built Scripts

### scripts/yijian.py
Industrial visual inspection (Baidu Qianfan AppBuilder). Requires `APPBUILDER_TOKEN`.

```bash
python3 scripts/yijian.py detect --image /path/to/product.jpg
python3 scripts/yijian.py report "Production line quality analysis"
```
