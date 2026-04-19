---
name: openakita/skills@baidu-ecommerce
description: "Cross-platform e-commerce skill for product comparison, review analysis, and purchase knowledge retrieval. Empowers agents with structured capabilities for price comparison, review analysis, and purchase guidance — a one-stop solution from product search to final purchase."
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
requires:
  env: [APPBUILDER_TOKEN, BAIDU_CLOUD_API_KEY, BAIDU_CLOUD_SECRET_KEY]
---

# Baidu E-Commerce

Empowers agents with structured capabilities for cross-platform price comparison, review analysis, and purchase knowledge — a one-stop solution for the complete e-commerce workflow, from product discovery to decision-making to checkout.

## When to Use

- Cross-platform product price comparison
- User review and sentiment analysis
- Purchase guidance and product recommendations
- End-to-end workflow from product search to order placement

## Prerequisites

Product price comparison and review analysis (Baidu Qianfan AppBuilder) requires setting `APPBUILDER_TOKEN`.

Product recommendations require setting `BAIDU_CLOUD_API_KEY` and `BAIDU_CLOUD_SECRET_KEY` (for Baidu Cloud APIs).

```bash
export APPBUILDER_TOKEN="your_token_here"
export BAIDU_CLOUD_API_KEY="your_api_key_here"
export BAIDU_CLOUD_SECRET_KEY="your_secret_key_here"
```

## Quick Start

```bash
python3 scripts/ecommerce.py review "Dyson Hair Dryer"
python3 scripts/ecommerce.py recommend "Noise-Canceling Headphones"
```
