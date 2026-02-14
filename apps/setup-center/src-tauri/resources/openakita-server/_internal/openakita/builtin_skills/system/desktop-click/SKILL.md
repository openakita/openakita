---
name: desktop-click
description: Click desktop elements or coordinates. When you need to click buttons/icons in applications, select menu items, or interact with desktop UI. Supports element description, name prefix, or coordinates. For browser webpage elements, use browser_click instead.
system: true
handler: desktop
tool-name: desktop_click
category: Desktop
---

# Desktop Click

点击桌面上的 UI 元素或指定坐标。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| target | string | 是 | 元素描述或坐标（如 '确定按钮' 或 '100,200'） |
| button | string | 否 | 鼠标按钮：left, right, middle，默认 left |
| double | boolean | 否 | 是否双击，默认 false |
| method | string | 否 | 查找方法：auto, uia, vision，默认 auto |

## Target Formats

- 元素描述：`"保存按钮"`、`"name:确定"`
- 坐标：`"100,200"`

## Find Methods

- `auto`: 自动选择（推荐）
- `uia`: 只用 UIAutomation
- `vision`: 只用视觉识别

## Examples

**点击按钮（元素描述）**:
```json
{"target": "确定按钮"}
```

**点击坐标**:
```json
{"target": "100,200"}
```

**右键点击**:
```json
{"target": "文件图标", "button": "right"}
```

**双击打开**:
```json
{"target": "文档.txt", "double": true}
```

## Notes

- 如果点击的是浏览器内的网页元素，请使用 `browser_click`
- 优先使用 UIAutomation（快速准确），失败时用视觉识别

## Related Skills

- `browser-click`: 点击浏览器网页元素
- `desktop-type`: 输入文本
- `desktop-find-element`: 先查找元素
