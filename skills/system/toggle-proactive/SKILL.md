---
name: toggle-proactive
description: Toggle proactive messaging mode. When enabled, Agent proactively sends greetings, task reminders, and key recaps via IM channel. Use when user asks to enable/disable proactive messages.
system: true
handler: persona
tool-name: toggle_proactive
category: Persona
---

# 活人感模式开关

## When to Use

- 用户要求开启/Close主动消息
- 用户说"别主动给我发消息了"
- 用户说"你可以主动跟我聊天"
- 用户在首次人格引导中选择开启

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| enabled | boolean | Yes | YesNoEnable |

## Features说明

开启后 Agent 会Via IM 通道主动Send：

- **早安问候**: 每天早上（7-9 点）
- **任务跟进**: 提醒未完成的任务
- **关键回顾**: 之前对话中的重要信息
- **闲聊问候**: 长时间未互动时
- **晚安提醒**: 晚上（仅亲近角色）

## 频率控制

- 每日最多 3 条（自适应调整）
- 两条之间至少间隔 2 小时
- 安静时段（23:00-07:00）不Send
- 根据用户反馈Automatic调整频率

## Examples

```
用户: "开启活人感模式"
→ toggle_proactive(enabled=true)

用户: "别主动给我发消息了"
→ toggle_proactive(enabled=false)

用户: "少发点消息"
→ update_persona_trait(dimension="proactiveness", preference="low")
```
