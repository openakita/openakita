---
name: openakita/skills@taobaoke-tool
description: "Taobao affiliate tool for product search, commission calculation, and marketing link generation. Use for e-commerce affiliate marketing and product recommendations."
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Taobao Affiliate Tool

Product search, commission tracking, and affiliate link generation tool for Taobao marketplace marketing.

## Configuration

Set your Taobao affiliate credentials:

```bash
export TAOBAO_APP_KEY="your_app_key"
export TAOBAO_APP_SECRET="your_app_secret"
export TAOBAO_ADZONE_ID="your_adzone_id"
```

## Features

- Product search and filtering
- Commission rate calculation
- Affiliate link generation
- Product category browsing
- Price comparison
- Trending product tracking

## Pre-built Scripts

### scripts/taobaoke.py
Taobao affiliate product search and link generation.

```bash
python3 scripts/taobaoke.py search "keyword" --price-min 50 --price-max 500
python3 scripts/taobaoke.py link --item-id 12345678
python3 scripts/taobaoke.py commission --category "electronics"
```
