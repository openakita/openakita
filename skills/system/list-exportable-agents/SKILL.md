---
name: list-exportable-agents
description: List all Agent profiles that can be exported as.akita-agent packages. Shows both system and custom agents.
system: true
handler: agent_package
tool-name: list_exportable_agents
category: Agent Package
---

# List Exportable Agents

listhave Agent, and Agent. 

## Parameters

No parameters.

## Returns

Returns Agent, Includes: 
- `id`: Agent ID
- `name`: Display
- `type`: (system/custom) 
- `category`:
- `skills_count`:

## Related Skills

- `export-agent`: Agent