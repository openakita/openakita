---
name: desktop-hotkey
description: Execute keyboard shortcuts. When you need to copy/paste (Ctrl+C/V), save files (Ctrl+S), close windows (Alt+F4), undo/redo (Ctrl+Z/Y), or select all (Ctrl+A).
system: true
handler: desktop
tool-name: desktop_hotkey
category: Desktop
---

# Desktop Hotkey

执行键盘快捷键。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| keys | array | 是 | 按键组合数组，如 ['ctrl', 'c'] |

## Common Shortcuts

| 快捷键 | 功能 |
|--------|------|
| ['ctrl', 'c'] | 复制 |
| ['ctrl', 'v'] | 粘贴 |
| ['ctrl', 'x'] | 剪切 |
| ['ctrl', 's'] | 保存 |
| ['ctrl', 'z'] | 撤销 |
| ['ctrl', 'y'] | 重做 |
| ['ctrl', 'a'] | 全选 |
| ['alt', 'f4'] | 关闭窗口 |
| ['alt', 'tab'] | 切换窗口 |
| ['win', 'd'] | 显示桌面 |

## Examples

**复制选中内容**:
```json
{"keys": ["ctrl", "c"]}
```

**保存文件**:
```json
{"keys": ["ctrl", "s"]}
```

## Related Skills

- `desktop-type`: 输入文本
- `desktop-click`: 点击元素
