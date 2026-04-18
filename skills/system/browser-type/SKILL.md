---
name: browser-type
description: Type text into input fields on webpage. When you need to fill forms, enter search queries, or input data. PREREQUISITE - must use browser_navigate first. May need to click field first for focus.
system: true
handler: browser
tool-name: browser_type
category: Browser
---

# Browser Type

在输入框中Type text。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| selector | string | Yes | 输入框的 CSS 选择器 |
| text | string | Yes | 要输入的文本 |

## Examples

**在search框输入**:
```json
{"selector": "input[name='q']", "text": "OpenAkita"}
```

**填写用户名**:
```json
{"selector": "#username", "text": "admin"}
```

## Prerequisites

- 必须先用 `browser_navigate` Open目标页面
- 如果输入框没有焦点，可能需要先Click

## Notes

- Supports中文输入
- 输入会追加到现有内容（如需清空请先选中）

## Related Skills

- `browser-navigate`: 先导航到页面
- `browser-click`: Click输入框get焦点


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
