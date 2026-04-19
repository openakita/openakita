---
name: openakita/skills@fliggy-travel
description: "FlyAI travel search and booking skill powered by Fliggy MCP. Search flights, hotels, attractions, trains, concerts, and travel deals with natural language. Supports diverse travel scenarios including individual, group, business, family trips. No API key required for basic features."
license: MIT
metadata:
 author: alibaba-flyai
 version: "1.0.14"
---

# FlyAI — Searchand

Via flyai-cli Call MCP, SupportsSearchand. 

## Installation

npm i -g @fly-ai/flyai-cli
flyai keyword-search --query "have "

API Key Use.: flyai config set FLYAI_API_KEY "your-key"

## Core

| | | |
|------|------|---------|
| keyword-search | Search | --query |
| ai-search | Search, | --query |
| search-flight | Search | --origin |
| search-hotel | Search | --dest-name |
| search-poi | Search | --city-name |
| search-train | Search | --origin |

## Output Format

have JSON, jq or Python. 

: 
- Includes:![]({picUrl})
- Includes: [Click]({jumpUrl})
- Use Markdown

## Usage Examples

flyai keyword-search --query ""
flyai search-hotel --dest-name "" --check-in 2026-04-10 --check-out 2026-04-12
flyai search-poi --city-name ""

## Pre-built Scripts

### scripts/setup.py
flyai-cli. 

```bash
python3 scripts/setup.py
```

### scripts/flyai_quick.py
Search. 

```bash
python3 scripts/flyai_quick.py search --keyword ""
python3 scripts/flyai_quick.py ai-search --query ""
python3 scripts/flyai_quick.py flight --from --to --date 2026-05-01
python3 scripts/flyai_quick.py hotel --city --checkin 2026-05-01
```