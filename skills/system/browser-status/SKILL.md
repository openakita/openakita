---
name: browser-status
description: Check browser current state including open status, current URL, page title, tab count. Useful for checking current page URL/title. Note - browser_open already includes status check and auto-starts if needed, so you don't need to call browser_status before browser_open.
system: true
handler: browser
tool-name: browser_status
category: Browser
---

# Browser Status

getcurrent status. 

## Parameters

No parameters.

## Returns

- `is_open`: YesNoOpen
- `url`: URL
- `title`:
- `tab_count`: Open

## Notes

- Used forView URL,, 
- `browser_open` Includes, notneed `browser_status` `browser_open`
- `browser_task` and `browser_navigate` willAutomaticLaunch browser, Manual

## Related Skills

- `browser-open`: DisplayRunCall
- `browser-navigate`:


## Recommendations

, Use `browser_task`. AutomaticandExecute Browser operations, ManualCall. 

: 
```python
browser_task(task="Opensearch")
```