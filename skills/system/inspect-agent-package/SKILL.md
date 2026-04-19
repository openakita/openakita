---
name: inspect-agent-package
description: Preview the contents of a .akita-agent package file without installing it. Shows manifest, profile, bundled skills, and validation status.
system: true
handler: agent_package
tool-name: inspect_agent_package
category: Agent Package
---

# Inspect Agent Package

`.akita-agent` File content, notExecuteinstall.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| package_path | string | Yes | .akita-agent File path |

## Returns

Returns:
- `manifest`:
- `profile`: Agent
- `bundled_skills`:
- `validation_errors`: (have)
- `id_conflict`: YesNoandhave Agent
- `package_size`:

## Related Skills

- `import-agent`:
