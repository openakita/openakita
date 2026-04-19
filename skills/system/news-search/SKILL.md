---
name: news-search
description: Search news using DuckDuckGo. Use when you need to find recent news articles, current events, or breaking news. Returns titles, sources, dates, URLs, and excerpts.
system: true
handler: web_search
tool-name: news_search
category: Web Search
---

# News Search

Use DuckDuckGo search, get. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| query | string | Yes | search |
| max_results | integer | No | Maximum (1-20, Default 5) |
| region | string | No | (wt-wt ) |
| safesearch | string | No | search (on/moderate/off) |
| timelimit | string | No | (d=, w=, m=) |

## Examples

**search**:
```json
{"query": "AI ", "max_results": 5}
```

**search**:
```json
{"query": "", "timelimit": "d"}
```

## Related Skills

- `web-search`: search