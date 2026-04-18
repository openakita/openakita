---
name: desktop-screenshot
description: Capture Windows desktop screenshot with automatic file saving. When you need to show desktop state, capture application windows, or record operation results. IMPORTANT - must actually call this tool, never say 'screenshot done' without calling. Returns file_path for deliver_artifacts.
system: true
handler: desktop
tool-name: desktop_screenshot
category: Desktop
---

# Desktop Screenshot

Capture Windows desktop screenshot。

## Important

**用户要求截图时，必须实际Call此工具。禁止不Call就说"截图完成"！**

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| path | string | No | Save路径（Optional,AutomaticGeneration） |
| window_title | string | No | 只Capture指定窗口（模糊匹配） |
| analyze | boolean | No | YesNo用视觉模型Analyze，Default false |
| analyze_query | string | No | Analyze查询（需要 analyze=true） |

## Examples

**Capture整个桌面**:
```json
{}
```

**Capture指定窗口**:
```json
{"window_title": "记事本"}
```

**Capture并Analyze**:
```json
{
  "analyze": true,
  "analyze_query": "找到所有按钮"
}
```

## Workflow

1. Call此工具截图
2. getReturns的 `file_path`
3. 用 `deliver_artifacts` Send给用户

## Notes

- 如果只涉及浏览器内的网页操作，请Use `browser_screenshot`
- 截图DefaultSave到用户桌面

## Related Skills

- `browser-screenshot`: Capture browser page
- `deliver-artifacts`: Send截图给用户
- `desktop-click`: Click桌面元素
