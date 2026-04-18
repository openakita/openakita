---
name: browser-open
description: Launch browser or check its status. Returns current state (is_open, url, title, tab_count). If already running, returns status without restarting. Auto-handles everything - no need to call browser_status first.
system: true
handler: browser
tool-name: browser_open
category: Browser
---

# Browser Open

Launch browser。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| visible | boolean | No | True=Display窗口, False=后台Run，Default True |
| ask_user | boolean | No | YesNo先询问用户偏好，Default False |

## Notes

- 如果浏览器已在Run，直接Returnscurrent status，不会重复Launch
- 服务重启后浏览器会Close，Call此工具会Automatic重新Launch
- 无需先Call `browser_status`，本工具已Includes状态检查

## Related Skills

- `browser-status`: 检查状态
- `browser-navigate`: 导航到页面


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
