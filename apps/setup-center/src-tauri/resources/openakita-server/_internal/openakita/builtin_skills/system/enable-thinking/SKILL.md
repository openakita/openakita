---
name: enable-thinking
description: Control deep thinking mode. Default enabled. For very simple tasks (simple reminders, greetings, quick queries), can temporarily disable to speed up response. Auto-restores to enabled after completion.
system: true
handler: system
tool-name: enable_thinking
category: System
---

# Enable Thinking

控制深度思考模式。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| enabled | boolean | 是 | 是否启用 thinking 模式 |
| reason | string | 是 | 简要说明原因 |

## Notes

- 默认状态：启用
- 可临时关闭的场景：简单提醒、简单问候、快速查询
- 完成后会自动恢复默认启用状态
- 复杂任务建议保持启用
