---
name: desktop-screenshot
description: Capture Windows desktop screenshot with automatic file saving. When you need to show desktop state, capture application windows, or record operation results. IMPORTANT - must actually call this tool, never say 'screenshot done' without calling. Returns file_path for deliver_artifacts.
system: true
handler: desktop
tool-name: desktop_screenshot
category: Desktop
---

# Desktop Screenshot

Capture Windows desktop screenshot. 

## Important

**need, Call. notCall""! **

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | No | Save (Optional,AutomaticGeneration) |
| window_title | string | No | Capture () |
| analyze | boolean | No | YesNoAnalyze, Default false |
| analyze_query | string | No | Analyze (need analyze=true) |

## Examples

**Capture**:
```json
{}
```

**Capture**:
```json
{"window_title": ""}
```

**CaptureAnalyze**:
```json
{
 "analyze": true,
"analyze_query": "have"
}
```

## Workflow

1. Call
2. getReturns `file_path`
3. `deliver_artifacts` Send

## Notes

-, Use `browser_screenshot`
- DefaultSave

## Related Skills

- `browser-screenshot`: Capture browser page
- `deliver-artifacts`: Send
- `desktop-click`: Click