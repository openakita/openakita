---
name: inspect-agent-package
description: Preview the contents of a .akita-agent package file without installing it. Shows manifest, profile, bundled skills, and validation status.
system: true
handler: agent_package
tool-name: inspect_agent_package
category: Agent Package
---

# Inspect Agent Package

预览 `.akita-agent` 包文件内容，不执行install。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| package_path | string | 是 | .akita-agent 包文件路径 |

## Returns

返回包详情：
- `manifest`: 包元数据
- `profile`: Agent 配置
- `bundled_skills`: 捆绑技能列表
- `validation_errors`: 校验错误（如有）
- `id_conflict`: 是否与本地已有 Agent 冲突
- `package_size`: 包文件大小

## Related Skills

- `import-agent`: 确认后导入
