---
name: browser-new-tab
description: Open new browser tab and navigate to URL (keeps current page open). When you need to open additional page without closing current, or multi-task across pages. PREREQUISITE - must confirm browser is running first.
system: true
handler: browser
tool-name: browser_new_tab
category: Browser
---

# Browser New Tab

打开新标签页并导航到指定 URL。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| url | string | 是 | 要在新标签页打开的 URL |

## Notes

- 不会覆盖当前页面
- 必须先确认浏览器已启动

## Related Skills

- `browser-status`: 检查浏览器状态
- `browser-navigate`: 在当前标签页导航
- `browser-switch-tab`: 切换标签页


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
