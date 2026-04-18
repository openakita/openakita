---
name: desktop-inspect
description: Inspect window UI element tree structure for debugging and understanding interface layout. When you need to debug UI automation issues, understand application structure, or find correct element identifiers.
system: true
handler: desktop
tool-name: desktop_inspect
category: Desktop
---

# Desktop Inspect

Inspect window's UI 元素树结构（Used for调试和了解界面结构）。

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| window_title | string | No | 窗口标题，不填则检查当前活动窗口 |
| depth | integer | No | 元素树遍历深度，Default 2 |

## Use Cases

- 调试 UI Automatic化问题
- 了解应用程序界面结构
- Find正确的元素标识符Used forClick/输入

## Examples

**检查当前窗口**:
```json
{}
```

**检查记事本，深度 3**:
```json
{"window_title": "记事本", "depth": 3}
```

## Returns

- 元素名称
- 元素类型
- 元素 ID
- 元素位置
- 子元素列表

## Related Skills

- `desktop-find-element`: Find特定元素
- `desktop-window`: 窗口manage
