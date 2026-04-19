---
name: get-chat-history
description: Get current chat history including user messages, your replies, and system task notifications. When user says 'check previous messages' or 'what did I just send', use this tool.
system: true
handler: im_channel
tool-name: get_chat_history
category: IM Channel
---

# Get Chat History

get .

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| limit | integer | No | getmessages, Default 20 |
| include_system | boolean | No | Whether to include system messages(), Default True |

## Returns

- Messages sent by user
- Your previous replies
- Notifications sent by system tasks

## When to Use

- " "
- " "
- need

## Related Skills

- `deliver-artifacts`: Send
