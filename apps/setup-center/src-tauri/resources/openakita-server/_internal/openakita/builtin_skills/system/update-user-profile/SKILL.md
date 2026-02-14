---
name: update-user-profile
description: Update user profile information when user shares preferences, habits, or work details. When you need to save user preferences, remember user's work domain, or provide personalized service.
system: true
handler: profile
tool-name: update_user_profile
category: User Profile
---

# Update User Profile

更新用户档案信息。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| key | string | 是 | 档案项键名 |
| value | string | 是 | 用户提供的信息值 |

## Supported Keys

- name: 称呼
- agent_role: Agent 角色
- work_field: 工作领域
- preferred_language: 编程语言偏好
- os: 操作系统
- ide: 开发工具
- detail_level: 详细程度偏好
- code_comment_lang: 代码注释语言
- work_hours: 工作时间
- timezone: 时区
- confirm_preference: 确认偏好

## Related Skills

- `get-user-profile`: 获取档案
- `skip-profile-question`: 跳过问题
