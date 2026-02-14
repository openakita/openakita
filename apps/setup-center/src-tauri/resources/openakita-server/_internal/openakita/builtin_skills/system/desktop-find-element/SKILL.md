---
name: desktop-find-element
description: Find desktop UI elements using UIAutomation (fast, accurate) or vision recognition (fallback). When you need to locate buttons/menus/icons, get element positions before clicking, or verify UI state. For browser webpage elements, use browser_* tools instead.
system: true
handler: desktop
tool-name: desktop_find_element
category: Desktop
---

# Desktop Find Element

查找桌面 UI 元素。优先使用 UIAutomation（快速准确），失败时用视觉识别（通用）。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| target | string | 是 | 元素描述，如 '保存按钮'、'name:文件'、'id:btn_ok' |
| window_title | string | 否 | 限定在某个窗口内查找 |
| method | string | 否 | 查找方法：auto（默认）、uia、vision |

## Supported Target Formats

- 自然语言："保存按钮"、"红色图标"
- 按名称："name:保存"
- 按 ID："id:btn_save"
- 按类型："type:Button"

## Find Methods

- `auto`: 自动选择（推荐）
- `uia`: 只用 UIAutomation
- `vision`: 只用视觉识别

## Returns

- 元素位置（x, y）
- 元素大小
- 元素属性

## Warning

如果操作的是浏览器内的网页元素，请使用 `browser_*` 工具。

## Related Skills

- `desktop-click`: 点击找到的元素
- `desktop-inspect`: 查看元素树结构
