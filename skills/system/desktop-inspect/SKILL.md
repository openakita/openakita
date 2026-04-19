---
name: desktop-inspect
description: Inspect window UI element tree structure for debugging and understanding interface layout. When you need to debug UI automation issues, understand application structure, or find correct element identifiers.
system: true
handler: desktop
tool-name: desktop_inspect
category: Desktop
---

# Desktop Inspect

Inspect window's UI (Used forand ). 

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| window_title | string | No |, not |
| depth | integer | No |, Default 2 |

## Use Cases

- UI Automatic
-
- Find Used forClick/

## Examples

****:
```json
{}
```

**, 3**:
```json
{"window_title": "", "depth": 3}
```

## Returns

-
-
- ID
-
-

## Related Skills

- `desktop-find-element`: Find
- `desktop-window`: manage