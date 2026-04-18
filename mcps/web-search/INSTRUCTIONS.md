# Web Search MCP Server

A web search service powered by DuckDuckGo — no API key required.

## Available Tools

### web_search - Web Search

Search the web and return titles, links, and summaries.

**Parameters**:
- `query` (required): Search keywords
- `max_results`: Number of results, default 5, maximum 20
- `region`: Region code
  - `wt-wt`: Global (default)
  - `cn-zh`: China
  - `us-en`: United States
- `safesearch`: Safe search level (`on`, `moderate`, `off`)

**Example**:
```json
{"query": "Python async programming tutorial", "max_results": 5, "region": "us-en"}
```

### news_search - News Search

Search for the latest news and return titles, sources, dates, links, and summaries.

**Parameters**:
- `query` (required): Search keywords
- `max_results`: Number of results, default 5, maximum 20
- `timelimit`: Time range
  - `d`: Past day
  - `w`: Past week
  - `m`: Past month

**Example**:
```json
{"query": "artificial intelligence AI", "max_results": 5, "timelimit": "w"}
```

## Usage Recommendations

1. **General information queries**: Use `web_search`
2. **Time-sensitive information**: Use `news_search`
3. **Region-specific results**: Set the appropriate `region` code for better localized results

## Relationship to the Built-in web_search

The system has both a built-in `web_search` tool and this MCP server. They share the same underlying search engine but serve different purposes:

- **Built-in web_search** — The primary path for Agent calls; faster response (no MCP connection overhead)
- **This MCP server** — Used for multi-node Org collaboration and external MCP client integrations

Agents should prefer the built-in `web_search` for everyday searches; this MCP is used when search capability needs to be exposed externally via the MCP protocol.
