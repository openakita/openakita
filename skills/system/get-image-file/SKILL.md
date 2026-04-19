---
name: get-image-file
description: Get local file path of image sent by user. When user sends image, system auto-downloads it. When you need to process user's image or analyze image content.
system: true
handler: im_channel
tool-name: get_image_file
category: IM Channel
---

# Get Image File

getSend 's local file path.

## Parameters

No parameters.

## Workflow

1. Send
2. System automatically downloads
3. UsegetFile path

## Related Skills

- `get-voice-file`: get
- `deliver-artifacts`: Send
