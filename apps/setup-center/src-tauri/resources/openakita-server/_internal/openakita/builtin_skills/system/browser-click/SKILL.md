---
name: browser-click
description: Click page elements by CSS selector or text content. When you need to click buttons, links, or select options. PREREQUISITE - must use browser_navigate to open target page first.
system: true
handler: browser
tool-name: browser_click
category: Browser
---

# Browser Click

点击页面上的元素。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| selector | string | 否 | CSS 选择器，如 '#btn-submit', '.button-class' |
| text | string | 否 | 元素文本，如 '提交', 'Submit' |

至少提供 `selector` 或 `text` 其中之一。

## Examples

**点击按钮（CSS 选择器）**:
```json
{"selector": "#submit-btn"}
```

**点击按钮（文本匹配）**:
```json
{"text": "提交"}
```

## Prerequisites

- 必须先用 `browser_navigate` 打开目标页面

## Related Skills

- `browser-navigate`: 先导航到页面
- `browser-type`: 在点击后输入文本


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
