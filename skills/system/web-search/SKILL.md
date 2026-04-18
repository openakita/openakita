---
name: web-search
description: Search the web using DuckDuckGo. Use when you need to find current information, verify facts, look up documentation, or answer questions requiring up-to-date knowledge. Returns titles, URLs, and snippets.
system: true
handler: web_search
tool-name: web_search
category: Web Search
---

# Web Search

Use DuckDuckGo search网页，get最新信息。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| query | string | Yes | search关键词 |
| max_results | integer | No | Maximum结果数（1-20，Default 5） |
| region | string | No | 地区代码（wt-wt 全球，cn-zh 中国） |
| safesearch | string | No | 安全search（on/moderate/off） |

## Examples

**search信息**:
```json
{"query": "Python asyncio 教程", "max_results": 5}
```

**search中文内容**:
```json
{"query": "天气预报", "region": "cn-zh"}
```

## Related Skills

- `news-search`: search新闻
