---
name: export-agent
description: Export a local Agent profile as a portable.akita-agent package file. Use when user wants to share, backup, or distribute an Agent with its skills and configuration.
system: true
handler: agent_package
tool-name: export_agent
category: Agent Package
---

# Export Agent

Agent `.akita-agent`, Includes Agent, and. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| profile_id | string | Yes | need Agent Profile ID |
| author_name | string | No | |
| version | string | No | (Default 1.0.0) |
| include_skills | array | No | need (Default Agent ) |

## Usage

`.akita-agent`: 
- SendUse
- Upload Agent Store
- Agent

## Related Skills

- `import-agent`: Import Agent
- `list-exportable-agents`: list Agent
- `inspect-agent-package`: