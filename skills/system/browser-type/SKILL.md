---
name: browser-type
description: Type text into input fields on webpage. When you need to fill forms, enter search queries, or input data. PREREQUISITE - must use browser_navigate first. May need to click field first for focus.
system: true
handler: browser
tool-name: browser_type
category: Browser
---

# Browser Type

inType text. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| selector | string | Yes | CSS |
| text | string | Yes | need |

## Examples

**insearch**:
```json
{"selector": "input[name='q']", "text": "OpenAkita"}
```

****:
```json
{"selector": "#username", "text": "admin"}
```

## Prerequisites

- `browser_navigate` Open
- have, needClick

## Notes

- Supports
- willhave () 

## Related Skills

- `browser-navigate`:
- `browser-click`: Clickget


## Recommendations

, Use `browser_task`. AutomaticandExecute Browser operations, ManualCall. 

: 
```python
browser_task(task="Opensearch")
```