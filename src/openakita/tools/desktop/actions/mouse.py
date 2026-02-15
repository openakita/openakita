"""
Windows 桌面自动化 - 鼠标操作模块

基于 PyAutoGUI 封装鼠标操作
"""

import logging
import sys
import time

from ..config import get_config
from ..types import ActionResult, BoundingBox, MouseButton, UIElement

# 平台检查
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

try:
    import pyautogui
except ImportError:
    from openakita.tools._import_helper import import_or_hint
    raise ImportError(import_or_hint("pyautogui"))

logger = logging.getLogger(__name__)


class MouseController:
    """
    鼠标控制器

    封装 PyAutoGUI 的鼠标操作，提供更友好的接口
    """

    def __init__(self):
        self._configure_pyautogui()

    def _configure_pyautogui(self) -> None:
        """配置 PyAutoGUI"""
        config = get_config().actions

        # 设置 failsafe（鼠标移到角落停止）
        pyautogui.FAILSAFE = config.failsafe

        # 设置操作间隔
        pyautogui.PAUSE = config.pause_between_actions

    def get_position(self) -> tuple[int, int]:
        """
        获取当前鼠标位置

        Returns:
            (x, y) 坐标
        """
        return pyautogui.position()

    def get_screen_size(self) -> tuple[int, int]:
        """
        获取屏幕尺寸

        Returns:
            (width, height)
        """
        return pyautogui.size()

    def _resolve_target(
        self,
        target: tuple[int, int] | UIElement | BoundingBox | str,
    ) -> tuple[int, int]:
        """
        解析目标位置

        Args:
            target: 可以是坐标元组、UIElement、BoundingBox 或 "x,y" 字符串

        Returns:
            (x, y) 坐标
        """
        if isinstance(target, tuple) and len(target) == 2:
            return target
        elif isinstance(target, UIElement):
            if target.center:
                return target.center
            raise ValueError(f"UIElement has no center position: {target}")
        elif isinstance(target, BoundingBox):
            return target.center
        elif isinstance(target, str):
            # 尝试解析 "x,y" 格式
            try:
                parts = target.split(",")
                if len(parts) == 2:
                    return (int(parts[0].strip()), int(parts[1].strip()))
            except (ValueError, IndexError):
                pass
            raise ValueError(f"Cannot parse target string: {target}")
        else:
            raise TypeError(f"Unsupported target type: {type(target)}")

    def move_to(
        self,
        x: int,
        y: int,
        duration: float | None = None,
    ) -> ActionResult:
        """
        移动鼠标到指定位置

        Args:
            x, y: 目标坐标
            duration: 移动持续时间（秒），None 使用配置

        Returns:
            ActionResult
        """
        config = get_config().actions
        dur = duration if duration is not None else config.move_duration

        start_time = time.time()
        try:
            pyautogui.moveTo(x, y, duration=dur)
            return ActionResult(
                success=True,
                action="move",
                target=f"{x},{y}",
                message=f"Moved mouse to ({x}, {y})",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to move mouse to ({x}, {y}): {e}")
            return ActionResult(
                success=False,
                action="move",
                target=f"{x},{y}",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def move_relative(
        self,
        dx: int,
        dy: int,
        duration: float | None = None,
    ) -> ActionResult:
        """
        相对移动鼠标

        Args:
            dx, dy: 相对偏移量
            duration: 移动持续时间

        Returns:
            ActionResult
        """
        config = get_config().actions
        dur = duration if duration is not None else config.move_duration

        start_time = time.time()
        try:
            pyautogui.move(dx, dy, duration=dur)
            return ActionResult(
                success=True,
                action="move_relative",
                target=f"{dx},{dy}",
                message=f"Moved mouse by ({dx}, {dy})",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to move mouse by ({dx}, {dy}): {e}")
            return ActionResult(
                success=False,
                action="move_relative",
                target=f"{dx},{dy}",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str | MouseButton = MouseButton.LEFT,
        clicks: int = 1,
        interval: float = 0.1,
    ) -> ActionResult:
        """
        点击鼠标

        Args:
            x, y: 点击位置，None 表示当前位置
            button: 鼠标按钮
            clicks: 点击次数
            interval: 多次点击之间的间隔

        Returns:
            ActionResult
        """
        config = get_config().actions
        btn = button.value if isinstance(button, MouseButton) else button

        start_time = time.time()
        try:
            # 点击前延迟
            if config.click_delay > 0:
                time.sleep(config.click_delay)

            if x is not None and y is not None:
                pyautogui.click(x, y, clicks=clicks, interval=interval, button=btn)
                target = f"{x},{y}"
            else:
                pyautogui.click(clicks=clicks, interval=interval, button=btn)
                pos = self.get_position()
                target = f"{pos[0]},{pos[1]}"

            action_name = "double_click" if clicks == 2 else "click"
            return ActionResult(
                success=True,
                action=action_name,
                target=target,
                message=f"Clicked {btn} button at ({target}), {clicks} time(s)",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to click at ({x}, {y}): {e}")
            return ActionResult(
                success=False,
                action="click",
                target=f"{x},{y}" if x and y else "current",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def click_target(
        self,
        target: tuple[int, int] | UIElement | BoundingBox | str,
        button: str | MouseButton = MouseButton.LEFT,
        clicks: int = 1,
    ) -> ActionResult:
        """
        点击目标

        Args:
            target: 目标（坐标、元素、边界框或字符串）
            button: 鼠标按钮
            clicks: 点击次数

        Returns:
            ActionResult
        """
        try:
            x, y = self._resolve_target(target)
            return self.click(x, y, button=button, clicks=clicks)
        except (ValueError, TypeError) as e:
            return ActionResult(
                success=False,
                action="click",
                target=str(target),
                error=str(e),
            )

    def double_click(
        self,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """
        双击

        Args:
            x, y: 点击位置，None 表示当前位置

        Returns:
            ActionResult
        """
        return self.click(x, y, clicks=2)

    def right_click(
        self,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """
        右键点击

        Args:
            x, y: 点击位置，None 表示当前位置

        Returns:
            ActionResult
        """
        return self.click(x, y, button=MouseButton.RIGHT)

    def middle_click(
        self,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """
        中键点击

        Args:
            x, y: 点击位置，None 表示当前位置

        Returns:
            ActionResult
        """
        return self.click(x, y, button=MouseButton.MIDDLE)

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.5,
        button: str | MouseButton = MouseButton.LEFT,
    ) -> ActionResult:
        """
        拖拽

        Args:
            start_x, start_y: 起始位置
            end_x, end_y: 结束位置
            duration: 拖拽持续时间
            button: 鼠标按钮

        Returns:
            ActionResult
        """
        btn = button.value if isinstance(button, MouseButton) else button

        start_time = time.time()
        try:
            # 先移动到起始位置
            pyautogui.moveTo(start_x, start_y)
            # 拖拽到目标位置
            pyautogui.drag(
                end_x - start_x,
                end_y - start_y,
                duration=duration,
                button=btn,
            )

            return ActionResult(
                success=True,
                action="drag",
                target=f"({start_x},{start_y}) -> ({end_x},{end_y})",
                message=f"Dragged from ({start_x},{start_y}) to ({end_x},{end_y})",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to drag: {e}")
            return ActionResult(
                success=False,
                action="drag",
                target=f"({start_x},{start_y}) -> ({end_x},{end_y})",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def drag_to(
        self,
        end_x: int,
        end_y: int,
        duration: float = 0.5,
        button: str | MouseButton = MouseButton.LEFT,
    ) -> ActionResult:
        """
        从当前位置拖拽到目标位置

        Args:
            end_x, end_y: 目标位置
            duration: 拖拽持续时间
            button: 鼠标按钮

        Returns:
            ActionResult
        """
        start_x, start_y = self.get_position()
        return self.drag(start_x, start_y, end_x, end_y, duration, button)

    def scroll(
        self,
        clicks: int,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """
        滚动鼠标滚轮

        Args:
            clicks: 滚动格数，正数向上，负数向下
            x, y: 滚动位置，None 表示当前位置

        Returns:
            ActionResult
        """
        start_time = time.time()
        try:
            if x is not None and y is not None:
                pyautogui.scroll(clicks, x, y)
                target = f"{x},{y}"
            else:
                pyautogui.scroll(clicks)
                target = "current"

            direction = "up" if clicks > 0 else "down"
            return ActionResult(
                success=True,
                action="scroll",
                target=target,
                message=f"Scrolled {direction} {abs(clicks)} clicks",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to scroll: {e}")
            return ActionResult(
                success=False,
                action="scroll",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def scroll_up(
        self,
        clicks: int = 3,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """向上滚动"""
        return self.scroll(abs(clicks), x, y)

    def scroll_down(
        self,
        clicks: int = 3,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """向下滚动"""
        return self.scroll(-abs(clicks), x, y)

    def hscroll(
        self,
        clicks: int,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """
        水平滚动（如果支持）

        Args:
            clicks: 滚动格数，正数向右，负数向左
            x, y: 滚动位置

        Returns:
            ActionResult
        """
        start_time = time.time()
        try:
            if x is not None and y is not None:
                pyautogui.hscroll(clicks, x, y)
            else:
                pyautogui.hscroll(clicks)

            direction = "right" if clicks > 0 else "left"
            return ActionResult(
                success=True,
                action="hscroll",
                message=f"Scrolled {direction} {abs(clicks)} clicks",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to horizontal scroll: {e}")
            return ActionResult(
                success=False,
                action="hscroll",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def mouse_down(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str | MouseButton = MouseButton.LEFT,
    ) -> ActionResult:
        """
        按下鼠标按钮（不释放）

        Args:
            x, y: 位置，None 表示当前位置
            button: 鼠标按钮

        Returns:
            ActionResult
        """
        btn = button.value if isinstance(button, MouseButton) else button

        start_time = time.time()
        try:
            if x is not None and y is not None:
                pyautogui.mouseDown(x, y, button=btn)
            else:
                pyautogui.mouseDown(button=btn)

            return ActionResult(
                success=True,
                action="mouse_down",
                message=f"Mouse {btn} button down",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action="mouse_down",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def mouse_up(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str | MouseButton = MouseButton.LEFT,
    ) -> ActionResult:
        """
        释放鼠标按钮

        Args:
            x, y: 位置，None 表示当前位置
            button: 鼠标按钮

        Returns:
            ActionResult
        """
        btn = button.value if isinstance(button, MouseButton) else button

        start_time = time.time()
        try:
            if x is not None and y is not None:
                pyautogui.mouseUp(x, y, button=btn)
            else:
                pyautogui.mouseUp(button=btn)

            return ActionResult(
                success=True,
                action="mouse_up",
                message=f"Mouse {btn} button up",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action="mouse_up",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )


# 全局实例
_mouse: MouseController | None = None


def get_mouse() -> MouseController:
    """获取全局鼠标控制器"""
    global _mouse
    if _mouse is None:
        _mouse = MouseController()
    return _mouse
