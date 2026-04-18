---
name: update-user-profile
description: Update user profile information when user shares preferences, habits, or work details. When you need to save user preferences, remember user's work domain, or provide personalized service.
system: true
handler: profile
tool-name: update_user_profile
category: User Profile
---

# Update User Profile

update用户档案信息。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| key | string | Yes | 档案项键名 |
| value | string | Yes | 用户Provides的信息值 |

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

- `get-user-profile`: get档案
- `skip-profile-question`: 跳过问题
