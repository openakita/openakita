---
name: get-voice-file
description: Get local file path of voice message sent by user. When user sends voice message, system auto-downloads it. When you need to process user's voice message or transcribe voice to text.
system: true
handler: im_channel
tool-name: get_voice_file
category: IM Channel
---

# Get Voice File

Get the local file path of a voice message sent by the user.

## Parameters

No parameters.

## Workflow

1. Send
2. System automatically downloads
3. UseGetFile path
4. Process with speech recognition script

## Related Skills

- `get-image-file`: Get
- `deliver-artifacts`: Send
