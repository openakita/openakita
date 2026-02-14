---
name: browser-type
description: Type text into input fields on webpage. When you need to fill forms, enter search queries, or input data. PREREQUISITE - must use browser_navigate first. May need to click field first for focus.
system: true
handler: browser
tool-name: browser_type
category: Browser
---

# Browser Type

在输入框中输入文本。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| selector | string | 是 | 输入框的 CSS 选择器 |
| text | string | 是 | 要输入的文本 |

## Examples

**在搜索框输入**:
```json
{"selector": "input[name='q']", "text": "OpenAkita"}
```

**填写用户名**:
```json
{"selector": "#username", "text": "admin"}
```

## Prerequisites

- 必须先用 `browser_navigate` 打开目标页面
- 如果输入框没有焦点，可能需要先点击

## Notes

- 支持中文输入
- 输入会追加到现有内容（如需清空请先选中）

## Related Skills

- `browser-navigate`: 先导航到页面
- `browser-click`: 点击输入框获取焦点


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
