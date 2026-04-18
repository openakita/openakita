---
name: inspect-agent-package
description: Preview the contents of a .akita-agent package file without installing it. Shows manifest, profile, bundled skills, and validation status.
system: true
handler: agent_package
tool-name: inspect_agent_package
category: Agent Package
---

# Inspect Agent Package

预览 `.akita-agent` 包File content，不Executeinstall。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| package_path | string | Yes | .akita-agent 包File path |

## Returns

Returns包详情：
- `manifest`: 包元数据
- `profile`: Agent 配置
- `bundled_skills`: 捆绑技能列表
- `validation_errors`: 校验错误（如有）
- `id_conflict`: YesNo与本地已有 Agent 冲突
- `package_size`: 包文件大小

## Related Skills

- `import-agent`: 确认后导入
