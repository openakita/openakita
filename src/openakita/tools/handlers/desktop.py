"""
Desktop Handler - Desktop automation tool handler

Handles tool calls related to Windows desktop automation:
- desktop_screenshot: Take a screenshot
- desktop_find_element: Find an element
- desktop_click: Click
- desktop_type: Type text
- desktop_hotkey: Keyboard shortcuts
- desktop_scroll: Scroll
- desktop_window: Window management
- desktop_wait: Wait for element/window
- desktop_inspect: Inspect element tree
"""

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Desktop tool list
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
    """Desktop automation tool handler"""

    TOOLS = DESKTOP_TOOLS

    def __init__(self, agent):
        self.agent = agent
        self._desktop_handler = None
        self._available = sys.platform == "win32"

    @property
    def desktop_handler(self):
        """Lazily initialize the desktop tool handler"""
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
        Handle a desktop tool call

        Args:
            tool_name: Tool name
            params: Tool parameters

        Returns:
            Execution result string
        """
        if not self._available:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("pyautogui")
            if hint:
                return f"Desktop tools unavailable: {hint}"
            return "Desktop tools are only available on Windows"

        handler = self.desktop_handler
        if handler is None:
            return "Desktop tool handler not initialized"

        try:
            result = await handler.handle(tool_name, params)
            return self._format_result(tool_name, result)
        except Exception as e:
            logger.error(f"Desktop tool error: {e}", exc_info=True)
            return f"Desktop tool error: {str(e)}"

    def _format_result(self, tool_name: str, result: Any) -> str:
        """Format tool execution result"""
        if isinstance(result, dict):
            if result.get("success"):
                # Screenshot result
                if result.get("file_path"):
                    output = f"Screenshot saved: {result.get('file_path')} ({result.get('width')}x{result.get('height')})"
                    if result.get("analysis"):
                        output += f"\n\nAnalysis result:\n{result['analysis'].get('answer', '')}"
                    return output

                # Element search result
                if result.get("found") is not None:
                    if result.get("found"):
                        elem = result.get("element", {})
                        return f"Element found: {elem.get('name', 'unknown')} @ {elem.get('center', 'unknown')}"
                    else:
                        return f"Element not found: {result.get('message', '')}"

                # Window list
                if result.get("windows"):
                    windows = result["windows"]
                    output = f"Found {len(windows)} windows:\n"
                    for i, w in enumerate(windows[:10], 1):
                        output += f"  {i}. {w.get('title', 'unknown')}\n"
                    if len(windows) > 10:
                        output += f"  ... and {len(windows) - 10} more\n"
                    return output

                # Element tree
                if result.get("tree"):
                    return f"Element tree:\n```\n{result.get('text', '')}\n```"

                # Generic success
                return f"{result.get('message', 'Operation succeeded')}"
            else:
                return f"Error: {result.get('error', 'Operation failed')}"
        else:
            return str(result)


def create_handler(agent) -> callable:
    """
    Create the desktop handler's handle method

    Args:
        agent: Agent instance

    Returns:
        Handler's handle method
    """
    handler = DesktopHandler(agent)
    return handler.handle
