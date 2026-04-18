---
name: news-search
description: Search news using DuckDuckGo. Use when you need to find recent news articles, current events, or breaking news. Returns titles, sources, dates, URLs, and excerpts.
system: true
handler: web_search
tool-name: news_search
category: Web Search
---

# News Search

Use DuckDuckGo search新闻，get最新资讯。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| query | string | Yes | search关键词 |
| max_results | integer | No | Maximum结果数（1-20，Default 5） |
| region | string | No | 地区代码（wt-wt 全球） |
| safesearch | string | No | 安全search（on/moderate/off） |
| timelimit | string | No | 时间范围（d=一天, w=一周, m=一月） |

## Examples

**search新闻**:
```json
{"query": "AI 最新进展", "max_results": 5}
```

**search今日新闻**:
```json
{"query": "科技", "timelimit": "d"}
```

## Related Skills

- `web-search`: search网页
