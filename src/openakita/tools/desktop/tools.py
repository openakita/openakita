"""
Windows 桌面自动化 - Agent 工具定义

定义供 OpenAkita Agent 使用的工具
"""

import logging
import sys
from typing import Any

# 平台检查
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

logger = logging.getLogger(__name__)


# ==================== 工具定义 ====================

DESKTOP_TOOLS = [
    {
        "name": "desktop_screenshot",
        "category": "Desktop",
        "description": "Capture Windows desktop screenshot with automatic file saving. When you need to: (1) Show user the desktop state, (2) Capture application windows, (3) Record operation results. IMPORTANT: Must actually call this tool - never say 'screenshot done' without calling. Returns file_path for deliver_artifacts. For browser-only screenshots, use browser_screenshot instead.",
        "detail": """截取 Windows 桌面屏幕截图并保存到文件。

⚠️ **重要警告**：
- 用户要求截图时，必须实际调用此工具
- 禁止不调用就说"截图完成"

**使用流程**：
1. 调用此工具截图
2. 获取返回的 file_path
3. 用 deliver_artifacts(artifacts=[{type:"image", path:file_path, caption:"..."}]) 交付给用户

**适用场景**：
- 桌面应用操作
- 查看整个桌面状态
- 桌面和浏览器混合操作

**可选功能**：
- window_title: 只截取指定窗口
- analyze: 用视觉模型分析截图内容

**注意**：如果只涉及浏览器内的网页操作，请使用 browser_screenshot。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "保存路径（可选），不填则自动生成 desktop_screenshot_YYYYMMDD_HHMMSS.png",
                },
                "window_title": {
                    "type": "string",
                    "description": "可选，只截取指定窗口（模糊匹配标题）",
                },
                "analyze": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否用视觉模型分析截图内容",
                },
                "analyze_query": {
                    "type": "string",
                    "description": "分析查询，如'找到所有按钮'（需要 analyze=true）",
                },
            },
            "required": [],
        },
    },
    {
        "name": "desktop_find_element",
        "category": "Desktop",
        "description": "Find desktop UI elements using UIAutomation (fast, accurate) or vision recognition (fallback). When you need to: (1) Locate buttons/menus/icons, (2) Get element positions before clicking, (3) Verify UI state. Supports: natural language ('save button'), name: prefix, id: prefix, type: prefix. For browser webpage elements, use browser_* tools instead.",
        "detail": """查找桌面 UI 元素。优先使用 UIAutomation（快速准确），失败时用视觉识别（通用）。

**支持的查找格式**：
- 自然语言："保存按钮"、"红色图标"
- 按名称："name:保存"
- 按 ID："id:btn_save"
- 按类型："type:Button"

**查找方法**：
- auto: 自动选择（推荐）
- uia: 只用 UIAutomation
- vision: 只用视觉识别

**返回信息**：
- 元素位置（x, y）
- 元素大小
- 元素属性

**注意**：如果操作的是浏览器内的网页元素，请使用 browser_* 工具。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "元素描述，如'保存按钮'、'name:文件'、'id:btn_ok'",
                },
                "window_title": {"type": "string", "description": "可选，限定在某个窗口内查找"},
                "method": {
                    "type": "string",
                    "enum": ["auto", "uia", "vision"],
                    "default": "auto",
                    "description": "查找方法：auto 自动选择，uia 只用 UIAutomation，vision 只用视觉",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "desktop_click",
        "category": "Desktop",
        "description": "Click desktop elements or coordinates. When you need to: (1) Click buttons/icons in applications, (2) Select menu items, (3) Interact with desktop UI. Supports: element description ('save button'), name: prefix, coordinates ('100,200'). Left/right/middle button and double-click supported. For browser webpage elements, use browser_task instead.",
        "detail": """点击桌面上的 UI 元素或指定坐标。

**支持的目标格式**：
- 元素描述："保存按钮"、"name:确定"
- 坐标："100,200"

**点击选项**：
- button: left/right/middle
- double: 是否双击

**元素查找方法**：
- auto: 自动选择（推荐）
- uia: 只用 UIAutomation
- vision: 只用视觉识别

**注意**：如果点击的是浏览器内的网页元素，请使用 browser_task 工具。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "元素描述（如'确定按钮'）或坐标（如'100,200'）",
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                    "description": "鼠标按钮",
                },
                "double": {"type": "boolean", "default": False, "description": "是否双击"},
                "method": {
                    "type": "string",
                    "enum": ["auto", "uia", "vision"],
                    "default": "auto",
                    "description": "元素查找方法",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "desktop_type",
        "category": "Desktop",
        "description": "Type text at current cursor position in desktop applications. When you need to: (1) Enter text in application dialogs, (2) Fill input fields, (3) Type in text editors. Supports Chinese input. Use clear_first=true to replace existing text. For browser webpage forms, use browser_task instead.",
        "detail": """在当前焦点位置输入文本。

**功能特点**：
- 支持中文输入
- 支持先清空再输入

**参数说明**：
- text: 要输入的文本
- clear_first: 是否先清空（Ctrl+A 后输入）

**使用建议**：
- 先点击目标输入框获得焦点
- 再调用此工具输入

**注意**：如果输入的是浏览器内的网页表单，请使用 browser_task 工具。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要输入的文本"},
                "clear_first": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否先清空现有内容（Ctrl+A 后输入）",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "desktop_hotkey",
        "category": "Desktop",
        "description": "Execute keyboard shortcuts. When you need to: (1) Copy/paste (Ctrl+C/V), (2) Save files (Ctrl+S), (3) Close windows (Alt+F4), (4) Undo/redo (Ctrl+Z/Y), (5) Select all (Ctrl+A). Common shortcuts: ['ctrl','c'], ['ctrl','v'], ['ctrl','s'], ['alt','f4'], ['ctrl','z'].",
        "detail": """执行键盘快捷键。

**常用快捷键**：
- ['ctrl', 'c']: 复制
- ['ctrl', 'v']: 粘贴
- ['ctrl', 'x']: 剪切
- ['ctrl', 's']: 保存
- ['ctrl', 'z']: 撤销
- ['ctrl', 'y']: 重做
- ['ctrl', 'a']: 全选
- ['alt', 'f4']: 关闭窗口
- ['alt', 'tab']: 切换窗口
- ['win', 'd']: 显示桌面

**参数格式**：
keys 是按键数组，如 ['ctrl', 'c']""",
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "按键组合，如 ['ctrl', 'c']、['alt', 'f4']",
                }
            },
            "required": ["keys"],
        },
    },
    {
        "name": "desktop_scroll",
        "category": "Desktop",
        "description": "Scroll mouse wheel in specified direction. When you need to: (1) Scroll page/document content, (2) Navigate long lists, (3) Zoom in/out (with Ctrl). Directions: up/down/left/right. Default amount is 3 scroll units.",
        "detail": """滚动鼠标滚轮。

**支持方向**：
- up: 向上滚动
- down: 向下滚动
- left: 向左滚动
- right: 向右滚动

**参数说明**：
- direction: 滚动方向
- amount: 滚动格数（默认 3）

**适用场景**：
- 滚动页面/文档内容
- 浏览长列表
- 配合 Ctrl 键缩放""",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "description": "滚动方向",
                },
                "amount": {"type": "integer", "default": 3, "description": "滚动格数"},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "desktop_window",
        "category": "Desktop",
        "description": "Window management operations. When you need to: (1) List all open windows, (2) Switch to a specific window, (3) Minimize/maximize/restore windows, (4) Close windows. Actions: list, switch, minimize, maximize, restore, close. Use title parameter for targeting specific window (fuzzy match).",
        "detail": """窗口管理操作。

**支持的操作**：
- list: 列出所有窗口
- switch: 切换到指定窗口（激活并置顶）
- minimize: 最小化窗口
- maximize: 最大化窗口
- restore: 恢复窗口
- close: 关闭窗口

**参数说明**：
- action: 操作类型（必填）
- title: 窗口标题（模糊匹配），list 操作不需要

**返回信息**（list 操作）：
- 窗口标题
- 窗口句柄
- 窗口位置和大小""",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "switch", "minimize", "maximize", "restore", "close"],
                    "description": "操作类型",
                },
                "title": {"type": "string", "description": "窗口标题（模糊匹配），list 操作不需要"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "desktop_wait",
        "category": "Desktop",
        "description": "Wait for UI element or window to appear. When you need to: (1) Wait for dialog to open, (2) Wait for loading to complete, (3) Synchronize with application state before next action. Target types: element (UI element), window (window title). Default timeout is 10 seconds.",
        "detail": """等待某个 UI 元素或窗口出现。

**适用场景**：
- 等待对话框打开
- 等待加载完成
- 在下一步操作前同步应用状态

**目标类型**：
- element: 等待 UI 元素
- window: 等待窗口

**参数说明**：
- target: 元素描述或窗口标题
- target_type: 目标类型（默认 element）
- timeout: 超时时间（默认 10 秒）

**返回结果**：
- 成功: 元素/窗口信息
- 超时: 错误信息""",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "元素描述或窗口标题"},
                "target_type": {
                    "type": "string",
                    "enum": ["element", "window"],
                    "default": "element",
                    "description": "目标类型",
                },
                "timeout": {"type": "integer", "default": 10, "description": "超时时间（秒）"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "desktop_inspect",
        "category": "Desktop",
        "description": "Inspect window UI element tree structure for debugging and understanding interface layout. When you need to: (1) Debug UI automation issues, (2) Understand application structure, (3) Find correct element identifiers for clicking/typing. Returns element names, types, and IDs at specified depth.",
        "detail": """检查窗口的 UI 元素树结构（用于调试和了解界面结构）。

**适用场景**：
- 调试 UI 自动化问题
- 了解应用程序界面结构
- 查找正确的元素标识符

**参数说明**：
- window_title: 窗口标题（不填则检查当前活动窗口）
- depth: 元素树遍历深度（默认 2）

**返回信息**：
- 元素名称
- 元素类型
- 元素 ID
- 元素位置
- 子元素列表""",
        "input_schema": {
            "type": "object",
            "properties": {
                "window_title": {
                    "type": "string",
                    "description": "窗口标题，不填则检查当前活动窗口",
                },
                "depth": {"type": "integer", "default": 2, "description": "元素树遍历深度"},
            },
            "required": [],
        },
    },
]


# ==================== 工具处理器 ====================


class DesktopToolHandler:
    """
    桌面工具处理器

    处理 Agent 的工具调用请求
    """

    def __init__(self):
        self._controller = None

    @property
    def controller(self):
        """懒加载控制器"""
        if self._controller is None:
            from .controller import get_controller

            self._controller = get_controller()
        return self._controller

    async def handle(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        处理工具调用

        Args:
            tool_name: 工具名称
            params: 参数字典

        Returns:
            结果字典
        """
        try:
            if tool_name == "desktop_screenshot":
                return await self._handle_screenshot(params)
            elif tool_name == "desktop_find_element":
                return await self._handle_find_element(params)
            elif tool_name == "desktop_click":
                return await self._handle_click(params)
            elif tool_name == "desktop_type":
                return self._handle_type(params)
            elif tool_name == "desktop_hotkey":
                return self._handle_hotkey(params)
            elif tool_name == "desktop_scroll":
                return self._handle_scroll(params)
            elif tool_name == "desktop_window":
                return self._handle_window(params)
            elif tool_name == "desktop_wait":
                return await self._handle_wait(params)
            elif tool_name == "desktop_inspect":
                return self._handle_inspect(params)
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return {"error": str(e)}

    async def _handle_screenshot(self, params: dict) -> dict:
        """处理截图请求"""
        import os
        from datetime import datetime

        path = params.get("path")
        window_title = params.get("window_title")
        analyze = params.get("analyze", False)
        analyze_query = params.get("analyze_query")

        # 截图
        img = self.controller.screenshot(window_title=window_title)

        # 生成保存路径
        if not path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"desktop_screenshot_{timestamp}.png"
            # 保存到用户桌面
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            if os.path.exists(desktop_path):
                path = os.path.join(desktop_path, filename)
            else:
                # 如果桌面不存在，保存到当前目录
                path = filename

        # 保存截图
        self.controller.capture.save(img, path)
        abs_path = os.path.abspath(path)

        result = {
            "success": True,
            "file_path": abs_path,
            "width": img.width,
            "height": img.height,
        }

        # 可选分析
        if analyze:
            analysis = await self.controller.analyze_screen(
                window_title=window_title,
                query=analyze_query,
            )
            result["analysis"] = analysis

        return result

    async def _handle_find_element(self, params: dict) -> dict:
        """处理查找元素请求"""
        target = params.get("target")
        window_title = params.get("window_title")
        method = params.get("method", "auto")

        element = await self.controller.find_element(
            target=target,
            window_title=window_title,
            method=method,
        )

        if element:
            return {
                "success": True,
                "found": True,
                "element": element.to_dict(),
            }
        else:
            return {
                "success": True,
                "found": False,
                "message": f"Element not found: {target}",
            }

    async def _handle_click(self, params: dict) -> dict:
        """处理点击请求"""
        target = params.get("target")
        button = params.get("button", "left")
        double = params.get("double", False)
        method = params.get("method", "auto")

        result = await self.controller.click(
            target=target,
            button=button,
            double=double,
            method=method,
        )

        return result.to_dict()

    def _handle_type(self, params: dict) -> dict:
        """处理输入请求"""
        text = params.get("text", "")
        clear_first = params.get("clear_first", False)

        result = self.controller.type_text(text, clear_first=clear_first)
        return result.to_dict()

    def _handle_hotkey(self, params: dict) -> dict:
        """处理快捷键请求"""
        keys = params.get("keys", [])

        if not keys:
            return {"error": "No keys provided"}

        result = self.controller.hotkey(*keys)
        return result.to_dict()

    def _handle_scroll(self, params: dict) -> dict:
        """处理滚动请求"""
        direction = params.get("direction", "down")
        amount = params.get("amount", 3)

        result = self.controller.scroll(direction, amount)
        return result.to_dict()

    def _handle_window(self, params: dict) -> dict:
        """处理窗口操作请求"""
        action = params.get("action")
        title = params.get("title")

        if action == "list":
            windows = self.controller.list_windows()
            return {
                "success": True,
                "windows": [w.to_dict() for w in windows],
                "count": len(windows),
            }

        result = self.controller.window_action(action, title)
        return result.to_dict()

    async def _handle_wait(self, params: dict) -> dict:
        """处理等待请求"""
        target = params.get("target")
        target_type = params.get("target_type", "element")
        timeout = params.get("timeout", 10)

        if target_type == "window":
            found = await self.controller.wait_for_window(target, timeout=timeout)
            return {
                "success": True,
                "found": found,
                "target": target,
                "target_type": "window",
            }
        else:
            element = await self.controller.wait_for_element(target, timeout=timeout)
            if element:
                return {
                    "success": True,
                    "found": True,
                    "element": element.to_dict(),
                }
            else:
                return {
                    "success": True,
                    "found": False,
                    "message": f"Element not found within {timeout}s: {target}",
                }

    def _handle_inspect(self, params: dict) -> dict:
        """处理检查请求"""
        window_title = params.get("window_title")
        depth = params.get("depth", 2)

        tree = self.controller.inspect(window_title=window_title, depth=depth)
        text = self.controller.inspect_text(window_title=window_title, depth=depth)

        return {
            "success": True,
            "tree": tree,
            "text": text,
        }


# 全局工具处理器
_handler: DesktopToolHandler | None = None


def get_tool_handler() -> DesktopToolHandler:
    """获取全局工具处理器"""
    global _handler
    if _handler is None:
        _handler = DesktopToolHandler()
    return _handler


def register_desktop_tools(agent: Any) -> None:
    """
    注册桌面工具到 Agent

    Args:
        agent: OpenAkita Agent 实例
    """
    handler = get_tool_handler()

    # 注册工具定义
    if hasattr(agent, "register_tools"):
        agent.register_tools(DESKTOP_TOOLS)

    # 注册处理器
    if hasattr(agent, "register_tool_handler"):
        for tool in DESKTOP_TOOLS:
            agent.register_tool_handler(tool["name"], handler.handle)

    logger.info(f"Registered {len(DESKTOP_TOOLS)} desktop tools")
