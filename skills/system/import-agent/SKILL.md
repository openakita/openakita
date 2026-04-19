---
name: import-agent
description: Import an Agent from a.akita-agent package file. Installs the Agent profile and any bundled skills to the local system.
system: true
handler: agent_package
tool-name: import_agent
category: Agent Package
---

# Import Agent

`.akita-agent` Import Agent, install Agent and. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| package_path | string | Yes |.akita-agent File path |
| force | boolean | No | ID YesNo (Default false) |

## Import Behavior

1. and
2. install `skills/custom/`
3. create Agent Profile (type custom) 
4. ID force, Automatic

## Related Skills

- `export-agent`: Export Agent
- `inspect-agent-package`: