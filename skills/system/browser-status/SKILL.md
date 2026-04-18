---
name: browser-status
description: Check browser current state including open status, current URL, page title, tab count. Useful for checking current page URL/title. Note - browser_open already includes status check and auto-starts if needed, so you don't need to call browser_status before browser_open.
system: true
handler: browser
tool-name: browser_status
category: Browser
---

# Browser Status

get浏览器current status。

## Parameters

No parameters.

## Returns

- `is_open`: 浏览器YesNoOpen
- `url`: 当前页面 URL
- `title`: 当前页面标题
- `tab_count`: Open的标签页数量

## Notes

- Used forView当前页面 URL、标题、标签页数量
- `browser_open` 已Includes状态检查，不需要先调 `browser_status` 再调 `browser_open`
- `browser_task` 和 `browser_navigate` 会AutomaticLaunch browser，无需Manual检查

## Related Skills

- `browser-open`: 如果状态Display未Run则Call
- `browser-navigate`: 状态检查后导航


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
