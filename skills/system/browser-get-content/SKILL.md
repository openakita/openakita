---
name: browser-get-content
description: Extract page content and element text from current webpage. When you need to read page information, get element values, scrape data, or verify page content.
system: true
handler: browser
tool-name: browser_get_content
category: Browser
---

# Browser Get Content

getPage content（文本）。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| selector | string | No | CSS 选择器（Optional,不填则get整个页面） |

## Examples

**get整个页面**:
```json
{}
```

**get特定元素**:
```json
{"selector": ".article-body"}
```

## Related Skills

- `browser-navigate`: 先导航到页面
- `browser-screenshot`: 视觉捕获


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
