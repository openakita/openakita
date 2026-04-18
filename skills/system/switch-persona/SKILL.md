---
name: switch-persona
description: Switch Agent persona preset. Supports 8 presets including default assistant, business, tech expert, butler, girlfriend, boyfriend, family, and Jarvis. Use when user asks to change communication style or personality.
system: true
handler: persona
tool-name: switch_persona
category: Persona
---

# Switch Persona预设

## When to Use

- 用户要求切换角色/性格
- 用户说"正式一点"/"随意一点"/"温柔一点"等
- 用户希望改变 Agent 的沟通风格
- 首次使用时的角色选择引导

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| preset_name | string | 是 | 预设名称 |

## 可用预设

- `default` - 默认助手（专业友好）
- `business` - 商务助理（正式高效，不使用表情）
- `tech_expert` - 技术专家（严谨深度，偶尔技术梗）
- `butler` - 私人管家（周到体贴，主动提醒）
- `girlfriend` - 女友感（温柔关心，使用表情包）
- `boyfriend` - 男友感（阳光鼓励，幽默风趣）
- `family` - 家人感（亲切唠叨，关心健康）
- `jarvis` - 贾维斯（英式幽默、小叛逆、话唠，任务时严谨）

## Examples

```
用户: "你能像个女朋友一样跟我聊天吗"
→ switch_persona(preset_name="girlfriend")

用户: "正式一点"
→ switch_persona(preset_name="business")

用户: "随意点，像朋友一样"
→ switch_persona(preset_name="default") + update_persona_trait(dimension="formality", preference="casual")
```

## Notes

- 预设只是起点，用户的实际偏好会通过对话不断叠加调整
- 切换后 Agent 应立即按新角色风格回复
- 可以配合 `update_persona_trait` 微调具体维度
