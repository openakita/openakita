---
name: desktop-type
description: Type text at current cursor position in desktop applications. When you need to enter text in dialogs, fill input fields, or type in text editors. Supports Chinese input. For browser webpage forms, use browser_type instead.
system: true
handler: desktop
tool-name: desktop_type
category: Desktop
---

# Desktop Type

Type text at current focus position。

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| text | string | Yes | 要输入的文本 |
| clear_first | boolean | No | YesNo先清空现有内容（Ctrl+A 后输入），Default false |

## Features

- Supports中文输入
- Supports先清空再输入

## Workflow

1. 先用 `desktop-click` Click目标输入框获得焦点
2. Call此工具Type text

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

如果输入的Yes浏览器内的网页表单，请Use `browser_type` 工具。

## Related Skills

- `desktop-click`: 先Clickget焦点
- `desktop-hotkey`: 快捷键操作
