---
name: browser-list-tabs
description: List all open browser tabs with their index, URL and title. When you need to check what pages are open, manage multiple tabs, or find a specific tab to switch to.
system: true
handler: browser
tool-name: browser_list_tabs
category: Browser
---

# Browser List Tabs

list所有Open的标签页。

## Parameters

No parameters.

## Returns

每个标签页的信息：
- 索引（从 0 开始）
- URL
- 页面标题

## Related Skills

- `browser-switch-tab`: Switch tab
- `browser-new-tab`: 新建标签页


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
