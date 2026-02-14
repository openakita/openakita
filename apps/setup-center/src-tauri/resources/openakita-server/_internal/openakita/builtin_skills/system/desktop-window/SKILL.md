---
name: desktop-window
description: Window management operations. When you need to list all open windows, switch to a specific window, minimize/maximize/restore windows, or close windows. Use title parameter for targeting specific window (fuzzy match).
system: true
handler: desktop
tool-name: desktop_window
category: Desktop
---

# Desktop Window

窗口管理操作。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| action | string | 是 | 操作类型：list/switch/minimize/maximize/restore/close |
| title | string | 否 | 窗口标题（模糊匹配），list 操作不需要 |

## Actions

| 操作 | 说明 | 需要 title |
|------|------|-----------|
| list | 列出所有窗口 | 否 |
| switch | 切换到指定窗口（激活并置顶） | 是 |
| minimize | 最小化窗口 | 是 |
| maximize | 最大化窗口 | 是 |
| restore | 恢复窗口 | 是 |
| close | 关闭窗口 | 是 |

## Examples

**列出所有窗口**:
```json
{"action": "list"}
```

**切换到记事本**:
```json
{"action": "switch", "title": "记事本"}
```

**最大化 Chrome**:
```json
{"action": "maximize", "title": "Chrome"}
```

## Returns (list action)

- 窗口标题
- 窗口句柄
- 窗口位置和大小

## Related Skills

- `desktop-screenshot`: 截取窗口
- `desktop-inspect`: 检查窗口结构
