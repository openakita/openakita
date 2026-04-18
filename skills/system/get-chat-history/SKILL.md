---
name: get-chat-history
description: Get current chat history including user messages, your replies, and system task notifications. When user says 'check previous messages' or 'what did I just send', use this tool.
system: true
handler: im_channel
tool-name: get_chat_history
category: IM Channel
---

# Get Chat History

get当前聊天的历史消息记录。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| limit | integer | No | get最近messages，Default 20 |
| include_system | boolean | No | Whether to include system messages（如任务通知），Default True |

## Returns

- Messages sent by user
- Your previous replies
- Notifications sent by system tasks

## When to Use

- 用户说"看看之前的消息"
- 用户说"刚才发的什么"
- 需要回顾对话上下文

## Related Skills

- `deliver-artifacts`: Send附件给用户
