---
name: search-hub-agents
description: Search for Agents on the OpenAkita Platform Agent Store
system: true
handler: agent_hub
tool-name: search_hub_agents
category: Platform
---

# search-hub-agents

Search for Agents on the OpenAkita Platform Agent Store.

## When to Use

- User wants to find or browse Agents on the OpenAkita marketplace
- User asks "have Agent " or "search XX Agent"
- User wants to discover community-shared Agents by category

## Workflow

1. Call `search_hub_agents` with optional filters
2. Review results — note the `id` field for each Agent
3. Use `get_hub_agent_detail` (from install-hub-agent) to inspect before installing
4. Use `install_hub_agent` to install the chosen Agent

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | No | Search keyword (e.g. "", "project manager") |
| `category` | No | Filter: customer_service, development, business, creative, education, productivity, general |
| `sort` | No | Sort by: downloads (default), rating, newest |
| `page` | No | Page number (default 1, 20 results per page) |

## Fallback

If the remote Agent Store is unavailable:
- Local Agent import/export via `.akita-agent` files still works
- Use `list_exportable_agents` to see local Agents
- Suggest user retry later or check network
