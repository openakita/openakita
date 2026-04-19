---
name: skip-profile-question
description: Skip profile question when user explicitly refuses to answer. When user says 'I don't want to answer' or 'skip this question', use this tool to stop asking about that item.
system: true
handler: profile
tool-name: skip_profile_question
category: User Profile
---

# Skip Profile Question

(not).

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| key | string | Yes | need |

## When to Use

- "not"
- "this"
- not

## Related Skills

- `update-user-profile`: update
- `get-user-profile`: get
