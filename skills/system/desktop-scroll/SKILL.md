---
name: desktop-scroll
description: Scroll mouse wheel in specified direction. When you need to scroll page/document content, navigate long lists, or zoom in/out with Ctrl. Directions - up/down/left/right.
system: true
handler: desktop
tool-name: desktop_scroll
category: Desktop
---

# Desktop Scroll

ScrollMouse wheel。

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| direction | string | Yes | Scroll方向：up/down/left/right |
| amount | integer | No | Scroll格数，Default 3 |

## Directions

- `up`: 向上Scroll
- `down`: 向下Scroll
- `left`: 向左Scroll
- `right`: 向右Scroll

## Examples

**向下Scroll**:
```json
{"direction": "down"}
```

**向上Scroll 5 格**:
```json
{"direction": "up", "amount": 5}
```

## Use Cases

- Scroll页面/文档内容
- 浏览长列表
- 配合 Ctrl 键缩放

## Related Skills

- `desktop-click`: ClickScroll区域
- `desktop-hotkey`: 快捷键操作
