---
name: browser-status
description: Check browser current state including open status, current URL, page title, tab count. Useful for checking current page URL/title. Note - browser_open already includes status check and auto-starts if needed, so you don't need to call browser_status before browser_open.
system: true
handler: browser
tool-name: browser_status
category: Browser
---

# Browser Status

获取浏览器当前状态。

## Parameters

无参数。

## Returns

- `is_open`: 浏览器是否打开
- `url`: 当前页面 URL
- `title`: 当前页面标题
- `tab_count`: 打开的标签页数量

## Notes

- 用于查看当前页面 URL、标题、标签页数量
- `browser_open` 已包含状态检查，不需要先调 `browser_status` 再调 `browser_open`
- `browser_task` 和 `browser_navigate` 会自动启动浏览器，无需手动检查

## Related Skills

- `browser-open`: 如果状态显示未运行则调用
- `browser-navigate`: 状态检查后导航


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```

