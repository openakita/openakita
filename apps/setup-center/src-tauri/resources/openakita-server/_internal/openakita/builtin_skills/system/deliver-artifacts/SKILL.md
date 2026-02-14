---
name: deliver-artifacts
description: Deliver artifacts (files/images/voice) to current IM chat via gateway, returning a receipt. Use this as the only delivery proof for attachments. Text replies are sent automatically - only use this for file/image/voice attachments.
system: true
handler: im_channel
tool-name: deliver_artifacts
category: IM Channel
---

# Deliver Artifacts

通过网关向当前 IM 聊天交付附件（文件/图片/语音），并返回结构化回执。

## Important

- **文本回复**由网关直接转发，不需要用工具发送
- **附件交付**必须使用本工具，回执是"已交付"的唯一证据

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| artifacts | array | 是 | 要交付的附件清单 |
| mode | string | 否 | send 或 preview（默认 send） |

### Artifact Item

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| type | string | 是 | file / image / voice |
| path | string | 是 | 本地文件路径 |
| caption | string | 否 | 说明文字 |

## Examples

**发送截图**:
```json
{
  "artifacts": [{"type": "image", "path": "data/temp/screenshot.png", "caption": "页面截图"}]
}
```

**发送文件**:
```json
{
  "artifacts": [{"type": "file", "path": "data/out/report.md"}]
}
```

## Related Skills

- `browser-screenshot`: 网页截图
- `desktop-screenshot`: 桌面截图
- `get-voice-file`: 获取语音文件
