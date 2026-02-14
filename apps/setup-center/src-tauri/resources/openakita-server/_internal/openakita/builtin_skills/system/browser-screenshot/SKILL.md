---
name: browser-screenshot
description: Capture browser page screenshot (webpage content only, not desktop). When you need to show page state, document results, or debug issues. For desktop screenshots, use desktop_screenshot instead.
system: true
handler: browser
tool-name: browser_screenshot
category: Browser
---

# Browser Screenshot

截取当前页面截图。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| path | string | 否 | 保存路径（可选，不填自动生成） |

## Examples

**截取当前页面**:
```json
{}
```

**保存到指定路径**:
```json
{"path": "C:/screenshots/result.png"}
```

## Notes

- 仅截取浏览器页面内容
- 如需截取桌面或其他应用，请使用 `desktop_screenshot`

## Workflow

1. 截图后获取 `file_path`
2. 使用 `send_to_chat(file_path=...)` 发送给用户

## Related Skills

- `desktop-screenshot`: 截取桌面应用
- `send-to-chat`: 发送截图给用户


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
