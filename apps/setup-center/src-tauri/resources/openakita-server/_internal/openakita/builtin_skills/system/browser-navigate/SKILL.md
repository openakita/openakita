---
name: browser-navigate
description: Navigate browser to specified URL to open a webpage. When you need to open webpages or start web automation. PREREQUISITE - must call before browser_click/type operations. Auto-starts browser if not running.
system: true
handler: browser
tool-name: browser_navigate
category: Browser
---

# Browser Navigate

导航到指定 URL，打开网页。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| url | string | 是 | 要访问的 URL（必须包含协议，如 https://） |

## Examples

**打开搜索引擎**:
```json
{"url": "https://www.google.com"}
```

**打开本地文件**:
```json
{"url": "file:///C:/Users/test.html"}
```

## Workflow

1. 调用此工具导航到目标页面
2. 等待页面加载
3. 使用 `browser_click` / `browser_type` 与页面交互

## Important Notes

- 必须在 `browser_click` / `browser_type` 之前调用此工具
- 如果浏览器未启动会自动启动
- URL 必须包含协议（http:// 或 https://）

## Related Skills

- `browser-status`: 检查浏览器状态
- `browser-click`: 点击页面元素
- `browser-type`: 在输入框输入文本


## 推荐

对于多步骤的浏览器任务，建议优先使用 `browser_task` 工具。它可以自动规划和执行复杂的浏览器操作，无需手动逐步调用各个工具。

示例：
```python
browser_task(task="打开百度搜索福建福州并截图")
```
