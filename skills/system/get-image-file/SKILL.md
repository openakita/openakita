---
name: get-image-file
description: Get local file path of image sent by user. When user sends image, system auto-downloads it. When you need to process user's image or analyze image content.
system: true
handler: im_channel
tool-name: get_image_file
category: IM Channel
---

# Get Image File

get用户发送的图片的本地文件路径。

## Parameters

无参数。

## Workflow

1. 用户发送图片
2. 系统自动下载到本地
3. 使用此工具get文件路径

## Related Skills

- `get-voice-file`: get语音文件
- `deliver-artifacts`: 发送文件给用户
