---
name: web-search
description: Search the web using DuckDuckGo. Use when you need to find current information, verify facts, look up documentation, or answer questions requiring up-to-date knowledge. Returns titles, URLs, and snippets.
system: true
handler: web_search
tool-name: web_search
category: Web Search
---

# Web Search

Use DuckDuckGo search, get. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| query | string | Yes | search |
| max_results | integer | No | Maximum (1-20, Default 5) |
| region | string | No | (wt-wt, cn-zh ) |
| safesearch | string | No | search (on/moderate/off) |

## Examples

**search**:
```json
{"query": "Python asyncio ", "max_results": 5}
```

**search**:
```json
{"query": "", "region": "cn-zh"}
```

## Related Skills

- `news-search`: search