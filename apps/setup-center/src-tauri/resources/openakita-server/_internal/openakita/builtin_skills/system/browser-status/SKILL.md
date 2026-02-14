---
name: browser-status
description: Check browser current state including open status, current URL, page title, tab count. IMPORTANT - must call before any browser task. Never assume browser is open from conversation history. Browser state resets on service restart.
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

## Important Notes

- **每次浏览器相关任务必须先调用此工具确认当前状态**
- 不能假设浏览器已打开
- 不能依赖历史记录，服务重启后浏览器会关闭

## Workflow

1. 调用 `browser_status` 检查状态
2. 如果未打开，调用 `browser_open` 启动
3. 调用 `browser_navigate` 导航到目标页面
4. 使用 `browser_click` / `browser_type` 交互

## Related Skills

- `browser-open`: 如果状态显示未运行则调用
- `browser-navigate`: 状态检查后导航


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
