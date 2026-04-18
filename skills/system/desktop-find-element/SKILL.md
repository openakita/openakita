---
name: desktop-find-element
description: Find desktop UI elements using UIAutomation (fast, accurate) or vision recognition (fallback). When you need to locate buttons/menus/icons, get element positions before clicking, or verify UI state. For browser webpage elements, use browser_* tools instead.
system: true
handler: desktop
tool-name: desktop_find_element
category: Desktop
---

# Desktop Find Element

Find桌面 UI 元素。优先Use UIAutomation（Quick准确），失败时用视觉识别（通用）。

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| target | string | Yes | 元素描述，如 'Save按钮'、'name:文件'、'id:btn_ok' |
| window_title | string | No | 限定在某个窗口内Find |
| method | string | No | Find方法：auto（Default）、uia、vision |

## Supported Target Formats

- 自然语言："Save按钮"、"红色图标"
- 按名称："name:Save"
- 按 ID："id:btn_save"
- 按类型："type:Button"

## Find Methods

- `auto`: Automatic选择（Recommendations）
- `uia`: 只用 UIAutomation
- `vision`: 只用视觉识别

## Returns

- 元素位置（x, y）
- 元素大小
- 元素属性

## Warning

如果操作的Yes浏览器内的网页元素，请Use `browser_*` 工具。

## Related Skills

- `desktop-click`: Click找到的元素
- `desktop-inspect`: View元素树结构
