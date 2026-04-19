---
name: browser-switch-tab
description: Switch to a specific browser tab by index. When you need to work with a different tab or return to previous page. Use browser_list_tabs to get tab indices.
system: true
handler: browser
tool-name: browser_switch_tab
category: Browser
---

# Browser Switch Tab

Switch. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| index | number | Yes | ( 0 ) |

## Workflow

1. `browser_list_tabs` gethave
2. UseReturns Switch

## Related Skills

- `browser-list-tabs`: get
- `browser-new-tab`:


## Recommendations

, Use `browser_task`. AutomaticandExecute Browser operations, ManualCall. 

: 
```python
browser_task(task="Opensearch")
```