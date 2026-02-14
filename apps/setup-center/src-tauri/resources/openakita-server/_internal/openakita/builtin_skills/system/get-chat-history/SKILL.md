---
name: get-chat-history
description: Get current chat history including user messages, your replies, and system task notifications. When user says 'check previous messages' or 'what did I just send', use this tool.
system: true
handler: im_channel
tool-name: get_chat_history
category: IM Channel
---

# Get Chat History

获取当前聊天的历史消息记录。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| limit | integer | 否 | 获取最近多少条消息，默认 20 |
| include_system | boolean | 否 | 是否包含系统消息（如任务通知），默认 True |

## Returns

- 用户发送的消息
- 你之前的回复
- 系统任务发送的通知

## When to Use

- 用户说"看看之前的消息"
- 用户说"刚才发的什么"
- 需要回顾对话上下文

## Related Skills

- `send-to-chat`: 发送消息
