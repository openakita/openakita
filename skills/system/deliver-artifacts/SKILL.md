---
name: deliver-artifacts
description: Deliver artifacts (files/images/voice) to current IM chat via gateway, returning a receipt. Use this as the only delivery proof for attachments. Text replies are sent automatically - only use this for file/image/voice attachments.
system: true
handler: im_channel
tool-name: deliver_artifacts
category: IM Channel
---

# Deliver Artifacts

Via网关Deliver attachments to current IM chat（文件/图片/语音），并Return structured receipt。

## Important

- **文本回复**由网关直接转发，不需要用工具Send
- **附件交付**必须Use本工具，回执Yes"已交付"的唯一证据

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| artifacts | array | Yes | List of artifacts to deliver |
| mode | string | No | send 或 preview（Default send） |

### Artifact Item

| 字段 | Type | Required | Description |
|-----|------|-----|------|
| type | string | Yes | file / image / voice |
| path | string | Yes | 本地File path |
| caption | string | No | 说明文字 |

## Examples

**Send截图**:
```json
{
  "artifacts": [{"type": "image", "path": "data/temp/screenshot.png", "caption": "页面截图"}]
}
```

**Send文件**:
```json
{
  "artifacts": [{"type": "file", "path": "data/out/report.md"}]
}
```

## Related Skills

- `browser-screenshot`: 网页截图
- `desktop-screenshot`: Desktop screenshot
- `get-voice-file`: get语音文件
