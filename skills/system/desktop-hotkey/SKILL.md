---
name: desktop-hotkey
description: Execute keyboard shortcuts. When you need to copy/paste (Ctrl+C/V), save files (Ctrl+S), close windows (Alt+F4), undo/redo (Ctrl+Z/Y), or select all (Ctrl+A).
system: true
handler: desktop
tool-name: desktop_hotkey
category: Desktop
---

# Desktop Hotkey

Execute keyboard shortcuts.

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| keys | array | Yes | ,  ['ctrl', 'c'] |

## Common Shortcuts

| | |
|--------|------|
| ['ctrl', 'c'] | |
| ['ctrl', 'v'] | |
| ['ctrl', 'x'] | |
| ['ctrl', 's'] | Save |
| ['ctrl', 'z'] | |
| ['ctrl', 'y'] | |
| ['ctrl', 'a'] | |
| ['alt', 'f4'] | Close |
| ['alt', 'tab'] | Switch |
| ['win', 'd'] | Display |

## Examples

****:
```json
{"keys": ["ctrl", "c"]}
```

**Save**:
```json
{"keys": ["ctrl", "s"]}
```

## Related Skills

- `desktop-type`: Type text
- `desktop-click`: Click
