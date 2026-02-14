---
name: desktop-scroll
description: Scroll mouse wheel in specified direction. When you need to scroll page/document content, navigate long lists, or zoom in/out with Ctrl. Directions - up/down/left/right.
system: true
handler: desktop
tool-name: desktop_scroll
category: Desktop
---

# Desktop Scroll

滚动鼠标滚轮。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| direction | string | 是 | 滚动方向：up/down/left/right |
| amount | integer | 否 | 滚动格数，默认 3 |

## Directions

- `up`: 向上滚动
- `down`: 向下滚动
- `left`: 向左滚动
- `right`: 向右滚动

## Examples

**向下滚动**:
```json
{"direction": "down"}
```

**向上滚动 5 格**:
```json
{"direction": "up", "amount": 5}
```

## Use Cases

- 滚动页面/文档内容
- 浏览长列表
- 配合 Ctrl 键缩放

## Related Skills

- `desktop-click`: 点击滚动区域
- `desktop-hotkey`: 快捷键操作
