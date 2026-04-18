---
name: import-agent
description: Import an Agent from a .akita-agent package file. Installs the Agent profile and any bundled skills to the local system.
system: true
handler: agent_package
tool-name: import_agent
category: Agent Package
---

# Import Agent

从 `.akita-agent` 包文件Import Agent，install Agent 配置和捆绑技能到本地。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| package_path | string | Yes | .akita-agent 包File path |
| force | boolean | No | 如果 ID 冲突YesNo强制覆盖（Default false） |

## Import Behavior

1. 校验包格式和安全性
2. install捆绑技能到 `skills/custom/` 目录
3. create Agent Profile（type 强制为 custom）
4. 如果 ID 冲突且未 force，Automatic追加后缀

## Related Skills

- `export-agent`: Export Agent 包
- `inspect-agent-package`: 导入前预览包内容
