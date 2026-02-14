---
name: browser-open
description: Launch and initialize browser for web automation. When you need to start web automation tasks or begin page interaction. IMPORTANT - must check browser_status first, browser closes on service restart.
system: true
handler: browser
tool-name: browser_open
category: Browser
---

# Browser Open

启动浏览器。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| visible | boolean | 否 | True=显示窗口, False=后台运行，默认 True |
| ask_user | boolean | 否 | 是否先询问用户偏好，默认 False |

## Important

- 服务重启后浏览器会关闭
- 必须先用 `browser_status` 检查状态
- 不能依赖历史记录假设浏览器已打开

## Related Skills

- `browser-status`: 检查状态
- `browser-navigate`: 导航到页面


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
