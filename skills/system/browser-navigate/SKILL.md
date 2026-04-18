---
name: browser-navigate
description: Navigate browser to specified URL to open a webpage. When you need to open webpages or start web automation. PREREQUISITE - must call before browser_click/type operations. Auto-starts browser if not running.
system: true
handler: browser
tool-name: browser_navigate
category: Browser
---

# Browser Navigate

Navigate to specified URL，Open网页。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| url | string | Yes | 要访问的 URL（必须Includes协议，如 https://） |

## Examples

**Opensearch引擎**:
```json
{"url": "https://www.google.com"}
```

**Open本地文件**:
```json
{"url": "file:///C:/Users/test.html"}
```

## Workflow

1. Call此工具导航到目标页面
2. 等待页面Load
3. Use `browser_click` / `browser_type` 与页面交互

## Important Notes

- 必须在 `browser_click` / `browser_type` 之前Call此工具
- 如果浏览器未Launch会AutomaticLaunch
- URL 必须Includes协议（http:// 或 https://）

## Related Skills

- `browser-status`: 检查Browser status
- `browser-click`: Click page element
- `browser-type`: 在输入框Type text


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
