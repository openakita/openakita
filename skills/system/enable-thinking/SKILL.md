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

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| enabled | boolean | Yes | YesNoEnable thinking 模式 |
| reason | string | Yes | 简要说明原因 |

## Notes

- Default状态：Enable
- 可临时Close的场景：简单提醒、简单问候、Quick查询
- 完成后会AutomaticResumeDefaultEnable状态
- 复杂任务建议保持Enable
