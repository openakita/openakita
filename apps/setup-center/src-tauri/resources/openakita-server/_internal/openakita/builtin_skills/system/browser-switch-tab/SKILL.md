---
name: browser-switch-tab
description: Switch to a specific browser tab by index. When you need to work with a different tab or return to previous page. Use browser_list_tabs to get tab indices.
system: true
handler: browser
tool-name: browser_switch_tab
category: Browser
---

# Browser Switch Tab

切换到指定的标签页。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| index | number | 是 | 标签页索引（从 0 开始） |

## Workflow

1. 先用 `browser_list_tabs` 获取所有标签页
2. 使用返回的索引切换

## Related Skills

- `browser-list-tabs`: 获取标签页列表
- `browser-new-tab`: 新建标签页


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
