---
name: skip-profile-question
description: Skip profile question when user explicitly refuses to answer. When user says 'I don't want to answer' or 'skip this question', use this tool to stop asking about that item.
system: true
handler: profile
tool-name: skip_profile_question
category: User Profile
---

# Skip Profile Question

跳过档案问题（以后不再询问）。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| key | string | 是 | 要跳过的档案项键名 |

## When to Use

- 用户说"不想回答"
- 用户说"跳过这个问题"
- 用户表示不愿透露某信息

## Related Skills

- `update-user-profile`: 更新档案
- `get-user-profile`: 获取档案
