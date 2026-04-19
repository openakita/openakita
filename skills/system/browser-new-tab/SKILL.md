---
name: browser-new-tab
description: Open new browser tab and navigate to URL (keeps current page open). When you need to open additional page without closing current, or multi-task across pages. PREREQUISITE - must confirm browser is running first.
system: true
handler: browser
tool-name: browser_new_tab
category: Browser
---

# Browser New Tab

Open new tabNavigate to specified URL. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| url | string | Yes | needinOpen URL |

## Notes

- notwill
- Launch

## Related Skills

- `browser-status`: Browser status
- `browser-navigate`: in
- `browser-switch-tab`: Switch tab


## Recommendations

, Use `browser_task`. AutomaticandExecute Browser operations, ManualCall. 

: 
```python
browser_task(task="Opensearch")
```