---
name: browser-navigate
description: Navigate browser to specified URL to open a webpage. When you need to open webpages or start web automation. PREREQUISITE - must call before browser_click/type operations. Auto-starts browser if not running.
system: true
handler: browser
tool-name: browser_navigate
category: Browser
---

# Browser Navigate

Navigate to specified URL, Open. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| url | string | Yes | need URL (Includes, https://) |

## Examples

**Opensearch**:
```json
{"url": "https://www.google.com"}
```

**Open**:
```json
{"url": "file:///C:/Users/test.html"}
```

## Workflow

1. Call
2. Load
3. Use `browser_click` / `browser_type` and

## Important Notes

- in `browser_click` / `browser_type` Call
- LaunchwillAutomaticLaunch
- URL Includes (http:// or https://) 

## Related Skills

- `browser-status`: Browser status
- `browser-click`: Click page element
- `browser-type`: inType text


## Recommendations

, Use `browser_task`. AutomaticandExecute Browser operations, ManualCall. 

: 
```python
browser_task(task="Opensearch")
```