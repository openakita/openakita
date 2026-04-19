---
name: deliver-artifacts
description: Deliver artifacts (files/images/voice) to current IM chat via gateway, returning a receipt. Use this as the only delivery proof for attachments. Text replies are sent automatically - only use this for file/image/voice attachments.
system: true
handler: im_channel
tool-name: deliver_artifacts
category: IM Channel
---

# Deliver Artifacts

ViaDeliver attachments to current IM chat (//), Return structured receipt. 

## Important

- ****, notneedSend
- ****Use, Yes""

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| artifacts | array | Yes | List of artifacts to deliver |
| mode | string | No | send or preview (Default send) |

### Artifact Item

| | Type | Required | Description |
|-----|------|-----|------|
| type | string | Yes | file / image / voice |
| path | string | Yes | File path |
| caption | string | No | |

## Examples

**Send**:
```json
{
"artifacts": [{"type": "image", "path": "data/temp/screenshot.png", "caption": ""}]
}
```

**Send**:
```json
{
 "artifacts": [{"type": "file", "path": "data/out/report.md"}]
}
```

## Related Skills

- `browser-screenshot`:
- `desktop-screenshot`: Desktop screenshot
- `get-voice-file`: get