---
name: browser-get-content
description: Extract page content and element text from current webpage. When you need to read page information, get element values, scrape data, or verify page content.
system: true
handler: browser
tool-name: browser_get_content
category: Browser
---

# Browser Get Content

获取页面内容（文本）。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| selector | string | 否 | CSS 选择器（可选，不填则获取整个页面） |

## Examples

**获取整个页面**:
```json
{}
```

**获取特定元素**:
```json
{"selector": ".article-body"}
```

## Related Skills

- `browser-navigate`: 先导航到页面
- `browser-screenshot`: 视觉捕获


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
