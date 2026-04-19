---
name: desktop-wait
description: Wait for UI element or window to appear. When you need to wait for dialog to open, loading to complete, or synchronize with application state before next action. Default timeout is 10 seconds.
system: true
handler: desktop
tool-name: desktop_wait
category: Desktop
---

# Desktop Wait

UI or. 

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| target | string | Yes | or |
| target_type | string | No |: element (Default) / window |
| timeout | integer | No | Timeout duration (), Default 10 |

## Target Types

- `element`: UI
- `window`:

## Use Cases

- Open
- Load
- in

## Examples

**Save**:
```json
{"target": "", "target_type": "window"}
```

****:
```json
{"target": "", "timeout": 5}
```

## Returns

-: /
-: 

## Related Skills

- `desktop-click`: Click
- `desktop-find-element`: Find