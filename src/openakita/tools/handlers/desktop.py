"""
Desktop Handler - 桌面自动化工具处理器

处理 Windows 桌面自动化相关的工具调用：
- desktop_screenshot: 截图
- desktop_find_element: 查找元素
- desktop_click: 点击
- desktop_type: 输入文本
- desktop_hotkey: 快捷键
- desktop_scroll: 滚动
- desktop_window: 窗口管理
- desktop_wait: 等待元素/窗口
- desktop_inspect: 检查元素树
"""

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# 桌面工具列表
DESKTOP_TOOLS = [
    "desktop_screenshot",
    "desktop_find_element",
    "desktop_click",
    "desktop_type",
    "desktop_hotkey",
    "desktop_scroll",
    "desktop_window",
    "desktop_wait",
    "desktop_inspect",
]


class DesktopHandler:
    """桌面自动化工具处理器"""

    def __init__(self, agent):
        self.agent = agent
        self._desktop_handler = None
        self._available = sys.platform == "win32"

    @property
    def desktop_handler(self):
        """懒加载桌面工具处理器"""
        if self._desktop_handler is None and self._available:
            try:
                from ..desktop.tools import DesktopToolHandler

                self._desktop_handler = DesktopToolHandler()
            except ImportError as e:
                from openakita.tools._import_helper import import_or_hint
                hint = import_or_hint("pyautogui") or str(e)
                logger.warning(f"Desktop tools not available: {hint}")
                self._available = False
        return self._desktop_handler

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """
        处理桌面工具调用

        Args:
            tool_name: 工具名称
            params: 工具参数

        Returns:
            执行结果字符串
        """
        if not self._available:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("pyautogui")
            if hint:
                return f"桌面工具不可用: {hint}"
            return "桌面工具仅在 Windows 上可用"

        handler = self.desktop_handler
        if handler is None:
            return "桌面工具处理器未初始化"

        try:
            result = await handler.handle(tool_name, params)
            return self._format_result(tool_name, result)
        except Exception as e:
            logger.error(f"Desktop tool error: {e}", exc_info=True)
            return f"桌面工具错误: {str(e)}"

    def _format_result(self, tool_name: str, result: Any) -> str:
        """格式化工具执行结果"""
        if isinstance(result, dict):
            if result.get("success"):
                # 截图结果
                if result.get("file_path"):
                    output = f"截图已保存: {result.get('file_path')} ({result.get('width')}x{result.get('height')})"
                    if result.get("analysis"):
                        output += f"\n\n分析结果:\n{result['analysis'].get('answer', '')}"
                    return output

                # 元素查找结果
                if result.get("found") is not None:
                    if result.get("found"):
                        elem = result.get("element", {})
                        return f"找到元素: {elem.get('name', 'unknown')} @ {elem.get('center', 'unknown')}"
                    else:
                        return f"未找到元素: {result.get('message', '')}"

                # 窗口列表
                if result.get("windows"):
                    windows = result["windows"]
                    output = f"找到 {len(windows)} 个窗口:\n"
                    for i, w in enumerate(windows[:10], 1):
                        output += f"  {i}. {w.get('title', 'unknown')}\n"
                    if len(windows) > 10:
                        output += f"  ... 还有 {len(windows) - 10} 个\n"
                    return output

                # 元素树
                if result.get("tree"):
                    return f"元素树:\n```\n{result.get('text', '')}\n```"

                # 通用成功
                return f"{result.get('message', '操作成功')}"
            else:
                return f"错误: {result.get('error', '操作失败')}"
        else:
            return str(result)


def create_handler(agent) -> callable:
    """
    创建桌面处理器的 handle 方法

    Args:
        agent: Agent 实例

    Returns:
        处理器的 handle 方法
    """
    handler = DesktopHandler(agent)
    return handler.handle
