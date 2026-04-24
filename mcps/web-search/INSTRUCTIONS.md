# Web Search MCP Server

基于 DuckDuckGo 的网络搜索服务，无需 API Key。

## 可用工具

### web_search - 网页搜索

搜索网页，返回标题、链接和摘要。

**参数**:
- `query` (必填): 搜索关键词
- `max_results`: 结果数量，默认 5，最大 20
- `region`: 地区代码
  - `wt-wt`: 全球（默认）
  - `cn-zh`: 中国
  - `us-en`: 美国
- `safesearch`: 安全搜索 (`on`, `moderate`, `off`)

**示例**:
```json
{"query": "Python 异步编程教程", "max_results": 5, "region": "cn-zh"}
```

### news_search - 新闻搜索

搜索最新新闻，返回标题、来源、日期、链接和摘要。

**参数**:
- `query` (必填): 搜索关键词
- `max_results`: 结果数量，默认 5，最大 20
- `timelimit`: 时间范围
  - `d`: 最近一天
  - `w`: 最近一周
  - `m`: 最近一个月

**示例**:
```json
{"query": "AI 人工智能", "max_results": 5, "timelimit": "w"}
```

## 使用建议

1. **一般信息查询**: 使用 `web_search`
2. **时效性信息**: 使用 `news_search`
3. **中文搜索**: 设置 `region: "cn-zh"` 获得更好的中文结果

## 与内置 web_search 的关系

系统同时存在内置 `web_search` 工具和本 MCP 服务器，二者底层共享同一搜索引擎，但定位不同：

- **内置 web_search** — Agent 直接调用的主路径，响应更快（无需 MCP 连接开销）
- **本 MCP 服务器** — 供 Org 多节点协作、外部 MCP 客户端集成使用

Agent 日常搜索应优先使用内置 `web_search`；本 MCP 在需要通过 MCP 协议对外暴露搜索能力时使用。

