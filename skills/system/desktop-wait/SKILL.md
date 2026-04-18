---
name: desktop-wait
description: Wait for UI element or window to appear. When you need to wait for dialog to open, loading to complete, or synchronize with application state before next action. Default timeout is 10 seconds.
system: true
handler: desktop
tool-name: desktop_wait
category: Desktop
---

# Desktop Wait

等待某个 UI 元素或窗口出现。

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| target | string | Yes | 元素描述或窗口标题 |
| target_type | string | No | 目标类型：element（Default）/ window |
| timeout | integer | No | Timeout duration（秒），Default 10 |

## Target Types

- `element`: 等待 UI 元素出现
- `window`: 等待窗口出现

## Use Cases

- 等待对话框Open
- 等待Load完成
- 在下一步操作前同步应用状态

## Examples

**等待Save对话框**:
```json
{"target": "另存为", "target_type": "window"}
```

**等待确定按钮**:
```json
{"target": "确定按钮", "timeout": 5}
```

## Returns

- 成功：元素/窗口信息
- 超时：错误信息

## Related Skills

- `desktop-click`: 等待后Click
- `desktop-find-element`: Find元素
