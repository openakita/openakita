---
name: get-voice-file
description: Get local file path of voice message sent by user. When user sends voice message, system auto-downloads it. When you need to process user's voice message or transcribe voice to text.
system: true
handler: im_channel
tool-name: get_voice_file
category: IM Channel
---

# Get Voice File

获取用户发送的语音消息的本地文件路径。

## Parameters

无参数。

## Workflow

1. 用户发送语音消息
2. 系统自动下载到本地
3. 使用此工具获取文件路径
4. 用语音识别脚本处理

## Related Skills

- `get-image-file`: 获取图片文件
- `send-to-chat`: 发送响应
