---
name: browser-screenshot
description: Capture browser page screenshot (webpage content only, not desktop). When you need to show page state, document results, or debug issues. For desktop screenshots, use desktop_screenshot instead.
system: true
handler: browser
tool-name: browser_screenshot
category: Browser
---

# Browser Screenshot

Capture current page screenshot。

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | No | Save路径（Optional,不填AutomaticGeneration） |

## Examples

**Capture current page**:
```json
{}
```

**Save到指定路径**:
```json
{"path": "C:/screenshots/result.png"}
```

## Notes

- 仅Capture browser page内容
- 如需Capture桌面或其他应用，请Use `desktop_screenshot`

## Workflow

1. 截图后get `file_path`
2. Use `deliver_artifacts` Send给用户

## Related Skills

- `desktop-screenshot`: Capture桌面应用
- `deliver-artifacts`: Send截图给用户


## Recommendations

对于多步骤的浏览器任务，建议优先Use `browser_task` 工具。它可以Automatic规划和Execute复杂的Browser operations，无需Manual逐步Call各个工具。

示例：
```python
browser_task(task="Open百度search福建福州并截图")
```
