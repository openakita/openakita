# Desktop Control MCP Server

基于视觉的桌面自动化服务，使用截屏 + pyautogui 实现桌面操控。

> 致谢：基于 [tech-shrimp/qwen_autogui](https://github.com/tech-shrimp/qwen_autogui) 开源项目的核心思路。

## 坐标体系

所有坐标使用 **0-1000 归一化** 体系：
- `(0, 0)` = 屏幕左上角
- `(1000, 1000)` = 屏幕右下角
- `(500, 500)` = 屏幕正中央

服务器内部自动将归一化坐标映射到实际屏幕分辨率。

## 典型工作流

```
1. capture_screen()        → 获取当前屏幕截图
2. 分析截图内容              → 确定操作目标位置
3. click(350, 200)         → 执行点击操作
4. capture_screen()        → 验证操作结果
5. 重复直到任务完成
```

## 可用工具

### capture_screen - 截取屏幕

截取完整屏幕截图，返回 base64 编码的 PNG 图片。
**在执行任何操作前先调用此工具了解屏幕内容。**

### get_screen_size - 获取屏幕分辨率

返回当前屏幕的宽度和高度（像素）。

### click - 点击

点击屏幕指定位置。

**参数**:
- `x` (必填): X 坐标 (0-1000)
- `y` (必填): Y 坐标 (0-1000)
- `button`: 鼠标按钮 (`left`, `right`, `middle`)，默认 `left`

### double_click - 双击

双击屏幕指定位置。

**参数**: `x`, `y` (0-1000)

### right_click - 右键点击

右键点击屏幕指定位置。

**参数**: `x`, `y` (0-1000)

### type_text - 输入文本

在当前光标位置输入文本。支持中文（通过剪贴板方式输入）。

**参数**:
- `text` (必填): 要输入的文本
- `interval`: 按键间隔（秒），默认 0.05

### press_keys - 按键组合

按下组合键。

**参数**:
- `keys` (必填): 按键数组，如 `["ctrl", "c"]`, `["alt", "f4"]`, `["enter"]`

### scroll - 滚动

滚动鼠标滚轮。

**参数**:
- `amount` (必填): 滚动量（正数=向上，负数=向下）
- `x`, `y` (可选): 滚动位置坐标 (0-1000)

### drag - 拖拽

从一个位置拖拽到另一个位置。

**参数**:
- `start_x`, `start_y` (必填): 起始坐标 (0-1000)
- `end_x`, `end_y` (必填): 结束坐标 (0-1000)
- `duration`: 拖拽时长（秒），默认 0.5

### move_mouse - 移动鼠标

移动鼠标到指定位置（不点击）。

**参数**:
- `x`, `y` (必填): 目标坐标 (0-1000)
- `duration`: 移动时长（秒），默认 0.3

## 使用示例

```
call_mcp_tool("desktop-control", "capture_screen", {})
call_mcp_tool("desktop-control", "click", {"x": 500, "y": 300})
call_mcp_tool("desktop-control", "type_text", {"text": "Hello World"})
call_mcp_tool("desktop-control", "press_keys", {"keys": ["ctrl", "s"]})
call_mcp_tool("desktop-control", "scroll", {"amount": -3, "x": 500, "y": 500})
call_mcp_tool("desktop-control", "drag", {"start_x": 100, "start_y": 100, "end_x": 500, "end_y": 500})
```

## 注意事项

1. **先截屏再操作** - 始终先用 `capture_screen` 了解当前屏幕状态
2. **坐标估算** - 根据截图中目标元素的视觉位置估算归一化坐标
3. **操作后验证** - 执行操作后再次截屏确认结果
4. **中文输入** - `type_text` 自动检测非 ASCII 字符，使用剪贴板方式输入
5. **安全性** - pyautogui 有内置的 failsafe（鼠标移到屏幕左上角可中断）

## 依赖

- `mss` - 屏幕截图
- `pyautogui` - GUI 自动化
- `pyperclip` (可选) - 中文输入支持
