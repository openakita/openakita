---
name: browser-new-tab
description: Open new browser tab and navigate to URL (keeps current page open). When you need to open additional page without closing current, or multi-task across pages. PREREQUISITE - must confirm browser is running first.
system: true
handler: browser
tool-name: browser_new_tab
category: Browser
---

# Browser New Tab

Open new tab并Navigate to specified URL。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| url | string | Yes | 要在新标签页Open的 URL |

## Notes

- 不会覆盖当前页面
- 必须先确认浏览器已Launch

## Related Skills

- `browser-status`: 检查Browser status
- `browser-navigate`: 在当前标签页导航
- `browser-switch-tab`: Switch tab


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
