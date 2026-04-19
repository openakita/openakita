---
name: browser-open
description: Launch browser or check its status. Returns current state (is_open, url, title, tab_count). If already running, returns status without restarting. Auto-handles everything - no need to call browser_status first.
system: true
handler: browser
tool-name: browser_open
category: Browser
---

# Browser Open

Launch browser. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| visible | boolean | No | True=Display, False=Run, Default True |
| ask_user | boolean | No | YesNo, Default False |

## Notes

- inRun, Returnscurrent status, notwillLaunch
- willClose, CallwillAutomaticLaunch
- Call `browser_status`, Includes

## Related Skills

- `browser-status`:
- `browser-navigate`:


## Recommendations

, Use `browser_task`. AutomaticandExecute Browser operations, ManualCall. 

: 
```python
browser_task(task="Opensearch")
```