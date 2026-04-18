---
name: web-search
description: Search the web using DuckDuckGo. Use when you need to find current information, verify facts, look up documentation, or answer questions requiring up-to-date knowledge. Returns titles, URLs, and snippets.
system: true
handler: web_search
tool-name: web_search
category: Web Search
---

# Web Search

使用 DuckDuckGo search网页，get最新信息。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| query | string | 是 | search关键词 |
| max_results | integer | 否 | 最大结果数（1-20，默认 5） |
| region | string | 否 | 地区代码（wt-wt 全球，cn-zh 中国） |
| safesearch | string | 否 | 安全search（on/moderate/off） |

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
