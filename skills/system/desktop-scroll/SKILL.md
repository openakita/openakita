---
name: desktop-scroll
description: Scroll mouse wheel in specified direction. When you need to scroll page/document content, navigate long lists, or zoom in/out with Ctrl. Directions - up/down/left/right.
system: true
handler: desktop
tool-name: desktop_scroll
category: Desktop
---

# Desktop Scroll

ScrollMouse wheel.

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| direction | string | Yes | Scroll:up/down/left/right |
| amount | integer | No | Scroll, Default 3 |

## Directions

- `up`: Scroll
- `down`: Scroll
- `left`: Scroll
- `right`: Scroll

## Examples

**Scroll**:
```json
{"direction": "down"}
```

**Scroll 5 **:
```json
{"direction": "up", "amount": 5}
```

## Use Cases

- Scroll/
-
- Ctrl

## Related Skills

- `desktop-click`: ClickScroll
- `desktop-hotkey`:
