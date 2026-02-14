---
name: browser-list-tabs
description: List all open browser tabs with their index, URL and title. When you need to check what pages are open, manage multiple tabs, or find a specific tab to switch to.
system: true
handler: browser
tool-name: browser_list_tabs
category: Browser
---

# Browser List Tabs

列出所有打开的标签页。

## Parameters

无参数。

## Returns

每个标签页的信息：
- 索引（从 0 开始）
- URL
- 页面标题

## Related Skills

- `browser-switch-tab`: 切换标签页
- `browser-new-tab`: 新建标签页


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
