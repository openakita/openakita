---
name: desktop-type
description: Type text at current cursor position in desktop applications. When you need to enter text in dialogs, fill input fields, or type in text editors. Supports Chinese input. For browser webpage forms, use browser_type instead.
system: true
handler: desktop
tool-name: desktop_type
category: Desktop
---

# Desktop Type

在当前焦点位置输入文本。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| text | string | 是 | 要输入的文本 |
| clear_first | boolean | 否 | 是否先清空现有内容（Ctrl+A 后输入），默认 false |

## Features

- 支持中文输入
- 支持先清空再输入

## Workflow

1. 先用 `desktop-click` 点击目标输入框获得焦点
2. 调用此工具输入文本

## Examples

**直接输入**:
```json
{"text": "Hello World"}
```

**清空后输入**:
```json
{"text": "New content", "clear_first": true}
```

## Warning

如果输入的是浏览器内的网页表单，请使用 `browser_type` 工具。

## Related Skills

- `desktop-click`: 先点击获取焦点
- `desktop-hotkey`: 快捷键操作
