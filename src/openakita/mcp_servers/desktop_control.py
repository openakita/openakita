"""
Desktop Control MCP 服务器

基于视觉的桌面自动化服务，通过截屏 + pyautogui 实现桌面操控。
支持点击、输入、拖拽、滚动、按键等操作，坐标使用 0-1000 归一化体系。

启动方式：
    python -m openakita.mcp_servers.desktop_control

致谢：
    基于 https://github.com/tech-shrimp/qwen_autogui 项目的核心思路，
    将桌面操控能力封装为 MCP 工具，感谢原作者 tech-shrimp 的开源贡献。
"""

import base64
import logging
import time
import traceback

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="desktop-control",
    instructions="""Desktop Control MCP Server - 桌面自动化操控服务。

通过截屏分析 + 动作执行实现桌面自动化。
坐标使用 0-1000 归一化体系：(0,0) 左上角，(1000,1000) 右下角。

典型工作流：
1. capture_screen() 获取截图
2. 分析截图，确定操作目标位置
3. 调用 click / type_text / press_keys 等执行操作
4. 再次 capture_screen() 验证结果

可用工具：
- capture_screen: 截取屏幕截图
- get_screen_size: 获取屏幕分辨率
- click: 点击指定位置
- double_click: 双击
- right_click: 右键点击
- type_text: 输入文本
- press_keys: 按键组合
- scroll: 滚动
- drag: 拖拽
- move_mouse: 移动鼠标
""",
)


def _get_screen_dimensions() -> tuple[int, int]:
    import pyautogui
    return pyautogui.size()


def _map_coordinates(x: float, y: float) -> tuple[int, int]:
    """将 0-1000 归一化坐标映射到实际屏幕分辨率"""
    w, h = _get_screen_dimensions()
    real_x = int(x / 1000 * w)
    real_y = int(y / 1000 * h)
    return real_x, real_y


@mcp.tool()
def capture_screen() -> str:
    """
    Capture a screenshot of the entire screen.

    Returns:
        Base64-encoded PNG image of the current screen.
        Use this to analyze what is displayed before performing actions.
    """
    try:
        import mss as mss_lib
        import mss.tools
    except ImportError:
        return "错误：缺少依赖 mss，请运行 pip install mss"

    try:
        with mss_lib.mss() as sct:
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            img_data = mss.tools.to_png(screenshot.rgb, screenshot.size)
            b64 = base64.b64encode(img_data).decode("utf-8")
            w, h = screenshot.size
            return (
                f"data:image/png;base64,{b64}\n\n"
                f"屏幕分辨率: {w}x{h}"
            )
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Screen capture failed: {e}\n{tb}")
        return f"截屏失败: {type(e).__name__}: {e}"


@mcp.tool()
def get_screen_size() -> str:
    """
    Get the screen resolution.

    Returns:
        Screen width and height in pixels.
    """
    try:
        w, h = _get_screen_dimensions()
        return f"屏幕分辨率: {w}x{h}"
    except Exception as e:
        return f"获取分辨率失败: {type(e).__name__}: {e}"


@mcp.tool()
def click(x: float, y: float, button: str = "left") -> str:
    """
    Click at a position on screen.

    Args:
        x: X coordinate (0-1000 normalized, 0=left edge, 1000=right edge)
        y: Y coordinate (0-1000 normalized, 0=top edge, 1000=bottom edge)
        button: Mouse button ("left", "right", "middle"), default "left"

    Returns:
        Result of the click action with actual pixel coordinates.
    """
    import pyautogui

    try:
        real_x, real_y = _map_coordinates(x, y)
        pyautogui.click(real_x, real_y, button=button)
        return f"已点击 ({real_x}, {real_y}) [归一化: ({x}, {y}), 按钮: {button}]"
    except Exception as e:
        return f"点击失败: {type(e).__name__}: {e}"


@mcp.tool()
def double_click(x: float, y: float) -> str:
    """
    Double-click at a position on screen.

    Args:
        x: X coordinate (0-1000 normalized)
        y: Y coordinate (0-1000 normalized)

    Returns:
        Result of the double-click action.
    """
    import pyautogui

    try:
        real_x, real_y = _map_coordinates(x, y)
        pyautogui.doubleClick(real_x, real_y)
        return f"已双击 ({real_x}, {real_y}) [归一化: ({x}, {y})]"
    except Exception as e:
        return f"双击失败: {type(e).__name__}: {e}"


@mcp.tool()
def right_click(x: float, y: float) -> str:
    """
    Right-click at a position on screen.

    Args:
        x: X coordinate (0-1000 normalized)
        y: Y coordinate (0-1000 normalized)

    Returns:
        Result of the right-click action.
    """
    import pyautogui

    try:
        real_x, real_y = _map_coordinates(x, y)
        pyautogui.rightClick(real_x, real_y)
        return f"已右击 ({real_x}, {real_y}) [归一化: ({x}, {y})]"
    except Exception as e:
        return f"右击失败: {type(e).__name__}: {e}"


@mcp.tool()
def type_text(text: str, interval: float = 0.05) -> str:
    """
    Type text at the current cursor position.
    For non-ASCII text (e.g. Chinese), uses clipboard-based input.

    Args:
        text: The text to type
        interval: Delay between keystrokes in seconds (default: 0.05, only for ASCII)

    Returns:
        Confirmation of the typed text.
    """
    import pyautogui

    try:
        if all(ord(c) < 128 for c in text):
            pyautogui.typewrite(text, interval=interval)
        else:
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)
        return f"已输入: {text}"
    except ImportError:
        try:
            pyautogui.typewrite(text, interval=interval)
            return f"已输入 (ASCII only): {text}"
        except Exception as e2:
            return f"输入失败: {type(e2).__name__}: {e2}"
    except Exception as e:
        return f"输入失败: {type(e).__name__}: {e}"


@mcp.tool()
def press_keys(keys: list[str]) -> str:
    """
    Press a key combination (hotkey).

    Args:
        keys: List of keys to press simultaneously.
              Examples: ["ctrl", "c"], ["alt", "f4"], ["enter"], ["ctrl", "shift", "escape"]

    Returns:
        Confirmation of the key press.
    """
    import pyautogui

    try:
        if isinstance(keys, str):
            keys = [keys]
        pyautogui.hotkey(*keys)
        return f"已按下: {'+'.join(keys)}"
    except Exception as e:
        return f"按键失败: {type(e).__name__}: {e}"


@mcp.tool()
def scroll(amount: int, x: float | None = None, y: float | None = None) -> str:
    """
    Scroll the mouse wheel.

    Args:
        amount: Scroll amount (positive=up, negative=down)
        x: Optional X coordinate (0-1000 normalized) to scroll at
        y: Optional Y coordinate (0-1000 normalized) to scroll at

    Returns:
        Confirmation of the scroll action.
    """
    import pyautogui

    try:
        if x is not None and y is not None:
            real_x, real_y = _map_coordinates(x, y)
            pyautogui.scroll(amount, x=real_x, y=real_y)
            return f"已滚动 {amount} 于 ({real_x}, {real_y})"
        else:
            pyautogui.scroll(amount)
            return f"已滚动 {amount}"
    except Exception as e:
        return f"滚动失败: {type(e).__name__}: {e}"


@mcp.tool()
def drag(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    duration: float = 0.5,
) -> str:
    """
    Drag from one position to another.

    Args:
        start_x: Start X coordinate (0-1000 normalized)
        start_y: Start Y coordinate (0-1000 normalized)
        end_x: End X coordinate (0-1000 normalized)
        end_y: End Y coordinate (0-1000 normalized)
        duration: Duration of the drag in seconds (default: 0.5)

    Returns:
        Confirmation of the drag action.
    """
    import pyautogui

    try:
        sx, sy = _map_coordinates(start_x, start_y)
        ex, ey = _map_coordinates(end_x, end_y)
        pyautogui.moveTo(sx, sy)
        pyautogui.drag(ex - sx, ey - sy, duration=duration)
        return f"已拖拽 ({sx}, {sy}) → ({ex}, {ey}), 耗时 {duration}s"
    except Exception as e:
        return f"拖拽失败: {type(e).__name__}: {e}"


@mcp.tool()
def move_mouse(x: float, y: float, duration: float = 0.3) -> str:
    """
    Move the mouse cursor to a position.

    Args:
        x: X coordinate (0-1000 normalized)
        y: Y coordinate (0-1000 normalized)
        duration: Duration of the movement in seconds (default: 0.3)

    Returns:
        Confirmation of the mouse movement.
    """
    import pyautogui

    try:
        real_x, real_y = _map_coordinates(x, y)
        pyautogui.moveTo(real_x, real_y, duration=duration)
        return f"鼠标已移至 ({real_x}, {real_y}) [归一化: ({x}, {y})]"
    except Exception as e:
        return f"移动失败: {type(e).__name__}: {e}"


if __name__ == "__main__":
    mcp.run()
