---
name: desktop-click
description: Click desktop elements or coordinates. When you need to click buttons/icons in applications, select menu items, or interact with desktop UI. Supports element description, name prefix, or coordinates. For browser webpage elements, use browser_click instead.
system: true
handler: desktop
tool-name: desktop_click
category: Desktop
---

# Desktop Click

Click桌面上的 UI 元素或指定坐标。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| target | string | Yes | 元素描述或坐标（如 '确定按钮' 或 '100,200'） |
| button | string | No | 鼠标按钮：left, right, middle，Default left |
| double | boolean | No | YesNo双击，Default false |
| method | string | No | Find方法：auto, uia, vision，Default auto |

## Target Formats

- 元素描述：`"Save按钮"`、`"name:确定"`
- 坐标：`"100,200"`

## Find Methods

- `auto`: Automatic选择（Recommendations）
- `uia`: 只用 UIAutomation
- `vision`: 只用视觉识别

## Examples

**Click按钮（元素描述）**:
```json
{"target": "确定按钮"}
```

**Click坐标**:
```json
{"target": "100,200"}
```

**右键Click**:
```json
{"target": "文件图标", "button": "right"}
```

**双击Open**:
```json
{"target": "文档.txt", "double": true}
```

## Notes

- 如果Click的Yes浏览器内的网页元素，请Use `browser_click`
- 优先Use UIAutomation（Quick准确），失败时用视觉识别

## Related Skills

- `browser-click`: Click浏览器网页元素
- `desktop-type`: Type text
- `desktop-find-element`: 先Find元素
