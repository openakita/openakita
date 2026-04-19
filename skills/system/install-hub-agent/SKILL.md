---
name: install-hub-agent
description: Download and install an Agent from the OpenAkita Platform Agent Store
system: true
handler: agent_hub
tool-name: install_hub_agent
category: Platform
---

# install-hub-agent

Download and install an Agent from the OpenAkita Platform Agent Store to the local system.

## When to Use

- User wants to install a specific Agent from the hub
- User says "installthis Agent" after browsing search results
- User provides an Agent ID to install

## Workflow

1. (Optional) Call `search_hub_agents` first to find the Agent
2. (Optional) Call `get_hub_agent_detail` to preview Agent details
3. Call `install_hub_agent` with the `agent_id`
4. The system will:
   - Download the `.akita-agent` package from the platform
   - Extract and install bundled skills (version-aware, skips if same/newer exists locally)
   - Fetch required external skills from their original GitHub repos
   - Register the Agent profile locally
   - Auto-reload skills so the Agent is immediately usable

## Tools

### install_hub_agent
| Parameter | Required | Description |
|-----------|----------|-------------|
| `agent_id` | Yes | The platform Agent ID (from search results) |
| `force` | No | Force overwrite if local ID conflict (default: false) |

### get_hub_agent_detail
| Parameter | Required | Description |
|-----------|----------|-------------|
| `agent_id` | Yes | The platform Agent ID to inspect |

## Important Notes

- Installed Agents appear in the local Agent list immediately
- Bundled skills go to `skills/custom/`, external skills go to `skills/community/`
- Skills are version-deduplicated: if a newer version already exists locally, it is kept
- Each installed skill gets a `.openakita-origin.json` tracking its source and version
- The Agent's `hub_source` field records where it came from

## Fallback

If the platform is unavailable, the user can still:
- Import Agents from `.akita-agent` files using `import_agent`
- Export local Agents using `export_agent`
