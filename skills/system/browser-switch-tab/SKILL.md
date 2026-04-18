---
name: browser-switch-tab
description: Switch to a specific browser tab by index. When you need to work with a different tab or return to previous page. Use browser_list_tabs to get tab indices.
system: true
handler: browser
tool-name: browser_switch_tab
category: Browser
---

# Browser Switch Tab

Switch到指定的标签页。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| index | number | Yes | 标签页索引（从 0 开始） |

## Workflow

1. 先用 `browser_list_tabs` get所有标签页
2. UseReturns的索引Switch

## Related Skills

- `browser-list-tabs`: get标签页列表
- `browser-new-tab`: 新建标签页


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
