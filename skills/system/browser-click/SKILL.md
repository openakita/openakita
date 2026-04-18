---
name: browser-click
description: Click page elements by CSS selector or text content. When you need to click buttons, links, or select options. PREREQUISITE - must use browser_navigate to open target page first.
system: true
handler: browser
tool-name: browser_click
category: Browser
---

# Browser Click

Click页面上的元素。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| selector | string | No | CSS 选择器，如 '#btn-submit', '.button-class' |
| text | string | No | 元素文本，如 '提交', 'Submit' |

至少Provides `selector` 或 `text` 其中之一。

## Examples

**Click按钮（CSS 选择器）**:
```json
{"selector": "#submit-btn"}
```

**Click按钮（文本匹配）**:
```json
{"text": "提交"}
```

## Prerequisites

- 必须先用 `browser_navigate` Open目标页面

## Related Skills

- `browser-navigate`: 先导航到页面
- `browser-type`: 在Click后Type text


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
