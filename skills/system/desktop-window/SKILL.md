---
name: desktop-window
description: Window management operations. When you need to list all open windows, switch to a specific window, minimize/maximize/restore windows, or close windows. Use title parameter for targeting specific window (fuzzy match).
system: true
handler: desktop
tool-name: desktop_window
category: Desktop
---

# Desktop Window

窗口manage操作。

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| action | string | Yes | 操作类型：list/switch/minimize/maximize/restore/close |
| title | string | No | 窗口标题（模糊匹配），list 操作不需要 |

## Actions

| 操作 | Description | 需要 title |
|------|------|-----------|
| list | list所有窗口 | No |
| switch | Switch到指定窗口（激活并置顶） | Yes |
| minimize | Minimum化窗口 | Yes |
| maximize | Maximum化窗口 | Yes |
| restore | Resume窗口 | Yes |
| close | Close窗口 | Yes |

## Examples

**list所有窗口**:
```json
{"action": "list"}
```

**Switch到记事本**:
```json
{"action": "switch", "title": "记事本"}
```

**Maximum化 Chrome**:
```json
{"action": "maximize", "title": "Chrome"}
```

## Returns (list action)

- 窗口标题
- 窗口句柄
- 窗口位置和大小

## Related Skills

- `desktop-screenshot`: Capture窗口
- `desktop-inspect`: Inspect window结构
