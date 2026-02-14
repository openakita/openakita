---
name: desktop-screenshot
description: Capture Windows desktop screenshot with automatic file saving. When you need to show desktop state, capture application windows, or record operation results. IMPORTANT - must actually call this tool, never say 'screenshot done' without calling. Returns file_path for send_to_chat.
system: true
handler: desktop
tool-name: desktop_screenshot
category: Desktop
---

# Desktop Screenshot

截取 Windows 桌面屏幕截图。

## Important

**用户要求截图时，必须实际调用此工具。禁止不调用就说"截图完成"！**

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| path | string | 否 | 保存路径（可选，自动生成） |
| window_title | string | 否 | 只截取指定窗口（模糊匹配） |
| analyze | boolean | 否 | 是否用视觉模型分析，默认 false |
| analyze_query | string | 否 | 分析查询（需要 analyze=true） |

## Examples

**截取整个桌面**:
```json
{}
```

**截取指定窗口**:
```json
{"window_title": "记事本"}
```

**截取并分析**:
```json
{
  "analyze": true,
  "analyze_query": "找到所有按钮"
}
```

## Workflow

1. 调用此工具截图
2. 获取返回的 `file_path`
3. 用 `send_to_chat(file_path=...)` 发送给用户

## Notes

- 如果只涉及浏览器内的网页操作，请使用 `browser_screenshot`
- 截图默认保存到用户桌面

## Related Skills

- `browser-screenshot`: 截取浏览器页面
- `send-to-chat`: 发送截图给用户
- `desktop-click`: 点击桌面元素
