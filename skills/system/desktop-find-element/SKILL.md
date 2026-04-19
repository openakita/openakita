---
name: desktop-find-element
description: Find desktop UI elements using UIAutomation (fast, accurate) or vision recognition (fallback). When you need to locate buttons/menus/icons, get element positions before clicking, or verify UI state. For browser webpage elements, use browser_* tools instead.
system: true
handler: desktop
tool-name: desktop_find_element
category: Desktop
---

# Desktop Find Element

Find UI. Use UIAutomation (Quick), (). 

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| target | string | Yes |, 'Save', 'name:', 'id:btn_ok' |
| window_title | string | No | inFind |
| method | string | No | Find: auto (Default), uia, vision |

## Supported Target Formats

-: "Save", ""
-: "name:Save"
- ID: "id:btn_save"
-: "type:Button"

## Find Methods

- `auto`: Automatic (Recommendations) 
- `uia`: UIAutomation
- `vision`:

## Returns

- (x, y) 
-
-

## Warning

Yes, Use `browser_*`. 

## Related Skills

- `desktop-click`: Click
- `desktop-inspect`: View