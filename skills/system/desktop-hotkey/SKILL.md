---
name: desktop-hotkey
description: Execute keyboard shortcuts. When you need to copy/paste (Ctrl+C/V), save files (Ctrl+S), close windows (Alt+F4), undo/redo (Ctrl+Z/Y), or select all (Ctrl+A).
system: true
handler: desktop
tool-name: desktop_hotkey
category: Desktop
---

# Desktop Hotkey

Execute keyboard shortcuts。

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| keys | array | Yes | 按键组合数组，如 ['ctrl', 'c'] |

## Common Shortcuts

| 快捷键 | 功能 |
|--------|------|
| ['ctrl', 'c'] | 复制 |
| ['ctrl', 'v'] | 粘贴 |
| ['ctrl', 'x'] | 剪切 |
| ['ctrl', 's'] | Save |
| ['ctrl', 'z'] | 撤销 |
| ['ctrl', 'y'] | 重做 |
| ['ctrl', 'a'] | 全选 |
| ['alt', 'f4'] | Close窗口 |
| ['alt', 'tab'] | Switch窗口 |
| ['win', 'd'] | Display桌面 |

## Examples

**复制选中内容**:
```json
{"keys": ["ctrl", "c"]}
```

**Save文件**:
```json
{"keys": ["ctrl", "s"]}
```

## Related Skills

- `desktop-type`: Type text
- `desktop-click`: Click元素
