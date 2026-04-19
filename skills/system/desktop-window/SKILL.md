---
name: desktop-window
description: Window management operations. When you need to list all open windows, switch to a specific window, minimize/maximize/restore windows, or close windows. Use title parameter for targeting specific window (fuzzy match).
system: true
handler: desktop
tool-name: desktop_window
category: Desktop
---

# Desktop Window

manage. 

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| action | string | Yes |: list/switch/minimize/maximize/restore/close |
| title | string | No | (), list notneed |

## Actions

| | Description | need title |
|------|------|-----------|
| list | listhave | No |
| switch | Switch () | Yes |
| minimize | Minimum | Yes |
| maximize | Maximum | Yes |
| restore | Resume | Yes |
| close | Close | Yes |

## Examples

**listhave**:
```json
{"action": "list"}
```

**Switch**:
```json
{"action": "switch", "title": ""}
```

**Maximum Chrome**:
```json
{"action": "maximize", "title": "Chrome"}
```

## Returns (list action)

-
-
- and

## Related Skills

- `desktop-screenshot`: Capture
- `desktop-inspect`: Inspect window