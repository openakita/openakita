---
name: desktop-click
description: Click desktop elements or coordinates. When you need to click buttons/icons in applications, select menu items, or interact with desktop UI. Supports element description, name prefix, or coordinates. For browser webpage elements, use browser_click instead.
system: true
handler: desktop
tool-name: desktop_click
category: Desktop
---

# Desktop Click

Click on desktop UI elements or screen coordinates.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| target | string | Yes | Element description or coordinates (e.g. `'Save File'` or `'100,200'`) |
| button | string | No | Mouse button: left, right, middle. Default: left |
| double | boolean | No | Double-click. Default: false |
| method | string | No | Find method: auto, uia, vision. Default: auto |

## Target Formats

- Element description: `"Save"`, `"name:Close"`
- Coordinates: `"100,200"`

## Find Methods

- `auto`: Automatic detection (recommended)
- `uia`: UI Automation (faster, Windows)
- `vision`: Visual screenshot matching (cross-platform)

## Examples

**Click by element description**:
```json
{"target": "Save"}
```

**Click by coordinates**:
```json
{"target": "100,200"}
```

**Right-click**:
```json
{"target": "file.txt", "button": "right"}
```

**Double-click to open**:
```json
{"target": "readme.txt", "double": true}
```

## Notes

- For browser page elements, use `browser_click` instead
- Use UI Automation for faster detection on Windows
- Vision mode works across all platforms but is slower

## Related Skills

- `browser-click`: Click browser page elements
- `desktop-type`: Type text on desktop
- `desktop-find-element`: Find desktop UI elements
