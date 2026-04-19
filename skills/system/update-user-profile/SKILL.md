---
name: update-user-profile
description: Update user profile information when user shares preferences, habits, or work details. When you need to save user preferences, remember user's work domain, or provide personalized service.
system: true
handler: profile
tool-name: update_user_profile
category: User Profile
---

# Update User Profile

update.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| key | string | Yes | |
| value | string | Yes | Provides |

## Supported Keys

- name:
- agent_role: Agent
- work_field:
- preferred_language:
- os:
- ide:
- detail_level:
- code_comment_lang:
- work_hours:
- timezone:
- confirm_preference:

## Related Skills

- `get-user-profile`: get
- `skip-profile-question`:
