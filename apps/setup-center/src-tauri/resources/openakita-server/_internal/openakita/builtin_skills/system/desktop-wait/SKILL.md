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

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| target | string | 是 | 元素描述或窗口标题 |
| target_type | string | 否 | 目标类型：element（默认）/ window |
| timeout | integer | 否 | 超时时间（秒），默认 10 |

## Target Types

- `element`: 等待 UI 元素出现
- `window`: 等待窗口出现

## Use Cases

- 等待对话框打开
- 等待加载完成
- 在下一步操作前同步应用状态

## Examples

**等待保存对话框**:
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

- `desktop-click`: 等待后点击
- `desktop-find-element`: 查找元素
