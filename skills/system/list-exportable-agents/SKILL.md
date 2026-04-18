---
name: list-exportable-agents
description: List all Agent profiles that can be exported as .akita-agent packages. Shows both system and custom agents.
system: true
handler: agent_package
tool-name: list_exportable_agents
category: Agent Package
---

# List Exportable Agents

list所有可导出的 Agent，包括系统预设和自定义 Agent。

## Parameters

无参数。

## Returns

返回 Agent 列表，每项包含：
- `id`: Agent ID
- `name`: 显示名称
- `type`: 类型（system/custom）
- `category`: 分类
- `skills_count`: 技能数量

## Related Skills

- `export-agent`: 导出指定 Agent
