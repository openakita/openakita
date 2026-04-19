---
name: search-store-skills
description: Search for Skills on the OpenAkita Platform Skill Store
system: true
handler: skill_store
tool-name: search_store_skills
category: Platform
---

# search-store-skills

Search for Skills on the OpenAkita Platform Skill Store.

## When to Use

- User wants to find or browse Skills on the OpenAkita marketplace
- User asks "haveinstall" or "search XX Skill"
- User wants to discover Skills by trust level (official / certified / community)

## Workflow

1. Call `search_store_skills` with optional filters
2. Review results — note the `id` and `trustLevel` for each Skill
3. Use `get_store_skill_detail` to inspect before installing
4. Use `install_store_skill` to install the chosen Skill

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | No | Search keyword |
| `category` | No | Filter by category |
| `trust_level` | No | Filter: official, certified, community |
| `sort` | No | Sort by: installs (default), rating, newest, stars |
| `page` | No | Page number (default 1) |

## Trust Levels

- **official**: Maintained by the OpenAkita team
- **certified**: Reviewed and approved by the team
- **community**: Community-contributed, use at your own discretion

## Fallback

If the remote Skill Store is unavailable:
- Local skill management still works
- Skills can be installed from GitHub using `install_skill`
- The skills.sh marketplace can be accessed via Setup Center
