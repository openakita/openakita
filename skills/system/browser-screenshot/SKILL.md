---
name: browser-screenshot
description: Capture browser page screenshot (webpage content only, not desktop). When you need to show page state, document results, or debug issues. For desktop screenshots, use desktop_screenshot instead.
system: true
handler: browser
tool-name: browser_screenshot
category: Browser
---

# Browser Screenshot

Capture current page screenshot. 

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | No | Save (Optional,notAutomaticGeneration) |

## Examples

**Capture current page**:
```json
{}
```

**Save**:
```json
{"path": "C:/screenshots/result.png"}
```

## Notes

- Capture browser page
- Captureor, Use `desktop_screenshot`

## Workflow

1. get `file_path`
2. Use `deliver_artifacts` Send

## Related Skills

- `desktop-screenshot`: Capture
- `deliver-artifacts`: Send


## Recommendations

, Use `browser_task`. AutomaticandExecute Browser operations, ManualCall. 

: 
```python
browser_task(task="Opensearch")
```