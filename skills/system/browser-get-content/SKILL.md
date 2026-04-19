---
name: browser-get-content
description: Extract page content and element text from current webpage. When you need to read page information, get element values, scrape data, or verify page content.
system: true
handler: browser
tool-name: browser_get_content
category: Browser
---

# Browser Get Content

getPage content (). 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| selector | string | No | CSS (Optional,notget) |

## Examples

**get**:
```json
{}
```

**get**:
```json
{"selector": ".article-body"}
```

## Related Skills

- `browser-navigate`:
- `browser-screenshot`:


## Recommendations

, Use `browser_task`. AutomaticandExecute Browser operations, ManualCall. 

: 
```python
browser_task(task="Opensearch")
```