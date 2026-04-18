---
name: get-image-file
description: Get local file path of image sent by user. When user sends image, system auto-downloads it. When you need to process user's image or analyze image content.
system: true
handler: im_channel
tool-name: get_image_file
category: IM Channel
---

# Get Image File

get用户Send的图片's local file path。

## Parameters

No parameters.

## Workflow

1. 用户Send图片
2. System automatically downloads到本地
3. Use此工具getFile path

## Related Skills

- `get-voice-file`: get语音文件
- `deliver-artifacts`: Send文件给用户
