---
name: deep-search
description: Deep research search returning hundreds of sources using Tavily and Exa APIs with parallel multi-query fan-out
version: 1.0.0
tags: [search, research, deep-search, tavily, exa]
author: OpenAkita
---

# Deep Search

Comprehensive deep research search that generates diverse sub-queries, fans them out in parallel across Tavily and Exa APIs, deduplicates by URL, and ranks by relevance.

## When to Use

- When you need **100-500+ unique sources** on a topic
- For deep market/competitive analysis
- Academic literature surveys
- Exhaustive source coverage for research reports

## When NOT to Use

- Quick lookups -> use `web_search` instead (5-20 results, faster)
- Single URL content -> use `web_fetch` or `browser_navigate`
- News only -> use `news_search`

## Configuration

Requires API keys in `.env`:
```
TAVILY_API_KEY=tvly-...
EXA_API_KEY=exa-...    # optional but recommended
```

## Usage

```
# Standard deep search (100 sources)
deep_search(query="transformer architecture NLP")

# Thorough research (400 sources with content)
deep_search(query="quantum computing patents 2026", max_sources=400, include_content=true)

# Single provider only
deep_search(query="AI safety research", providers=["exa"])
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| query | string | required | Research topic |
| max_sources | int | 100 | Target unique sources (50-500) |
| providers | list | ["tavily","exa"] | Providers to use |
| include_content | bool | false | Fetch full content (slower) |
| max_display | int | 50 | Max sources in output (0=all) |

## Architecture

```
Query -> expand_queries() -> 15-30 diverse sub-queries
  -> Tavily: parallel batches of 5 (search_depth=advanced)
  -> Exa: parallel batches of 5 (neural search + highlights)
  -> Deduplicate by URL hash -> Rank by relevance -> Return top N
```

## Output Format

Returns a structured report with:
- Total unique sources found
- Duplicates removed count
- Providers used
- Per-source: title, URL, snippet, relevance score, provider
