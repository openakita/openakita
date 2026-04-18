---
name: export-agent
description: Export a local Agent profile as a portable .akita-agent package file. Use when user wants to share, backup, or distribute an Agent with its skills and configuration.
system: true
handler: agent_package
tool-name: export_agent
category: Agent Package
---

# Export Agent

将本地 Agent 导出为 `.akita-agent` 包文件，Includes Agent 配置、提示词和捆绑技能。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| profile_id | string | Yes | 要导出的 Agent Profile ID |
| author_name | string | No | 作者名称 |
| version | string | No | 版本号（Default 1.0.0） |
| include_skills | array | No | 要打包的技能列表（Default为 Agent 配置中的本地技能） |

## Usage

导出后的 `.akita-agent` 文件可以：
- Send给其他用户导入Use
- Upload到 Agent Store 分享
- 作为 Agent 备份

## Related Skills

- `import-agent`: Import Agent 包
- `list-exportable-agents`: list可导出的 Agent
- `inspect-agent-package`: 预览包内容
