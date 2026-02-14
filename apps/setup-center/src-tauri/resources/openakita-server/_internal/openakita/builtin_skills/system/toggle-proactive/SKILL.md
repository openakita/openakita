---
name: toggle-proactive
description: 开关活人感模式。开启后 Agent 会主动发送问候、任务提醒、关键回顾等消息。
system: true
handler: persona
tool-name: toggle_proactive
category: Persona
---

# 活人感模式开关

## 何时使用

- 用户要求开启/关闭主动消息
- 用户说"别主动给我发消息了"
- 用户说"你可以主动跟我聊天"
- 用户在首次人格引导中选择开启

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| enabled | boolean | 是 | 是否启用 |

## 功能说明

开启后 Agent 会通过 IM 通道主动发送：

- **早安问候**: 每天早上（7-9 点）
- **任务跟进**: 提醒未完成的任务
- **关键回顾**: 之前对话中的重要信息
- **闲聊问候**: 长时间未互动时
- **晚安提醒**: 晚上（仅亲近角色）

## 频率控制

- 每日最多 3 条（自适应调整）
- 两条之间至少间隔 2 小时
- 安静时段（23:00-07:00）不发送
- 根据用户反馈自动调整频率

## 示例

```
用户: "开启活人感模式"
→ toggle_proactive(enabled=true)

用户: "别主动给我发消息了"
→ toggle_proactive(enabled=false)

用户: "少发点消息"
→ update_persona_trait(dimension="proactiveness", preference="low")
```
