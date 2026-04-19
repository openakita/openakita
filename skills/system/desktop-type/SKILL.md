---
name: desktop-type
description: Type text at current cursor position in desktop applications. When you need to enter text in dialogs, fill input fields, or type in text editors. Supports Chinese input. For browser webpage forms, use browser_type instead.
system: true
handler: desktop
tool-name: desktop_type
category: Desktop
---

# Desktop Type

Type text at current focus position. 

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| text | string | Yes | need |
| clear_first | boolean | No | YesNohave (Ctrl+A ), Default false |

## Features

- Supports
- Supports

## Workflow

1. `desktop-click` Click
2. CallType text

## Examples

****:
```json
{"text": "Hello World"}
```

****:
```json
{"text": "New content", "clear_first": true}
```

## Warning

Yes, Use `browser_type`. 

## Related Skills

- `desktop-click`: Clickget
- `desktop-hotkey`: