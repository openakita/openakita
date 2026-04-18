"""
Windows desktop automation - mouse operations module.

Provides mouse operations built on top of PyAutoGUI.
"""

import logging
import sys
import time

from ..config import get_config
from ..types import ActionResult, BoundingBox, MouseButton, UIElement

# Platform check
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
    Mouse controller.

    Wraps PyAutoGUI mouse operations with a friendlier interface.
    """

    def __init__(self):
        self._configure_pyautogui()

    def _configure_pyautogui(self) -> None:
        """Configure PyAutoGUI settings."""
        config = get_config().actions

        # Set failsafe (moving mouse to corner aborts)
        pyautogui.FAILSAFE = config.failsafe

        # Set action interval
        pyautogui.PAUSE = config.pause_between_actions

    def get_position(self) -> tuple[int, int]:
        """
        Get current mouse position.

        Returns:
            (x, y) coordinates.
        """
        return pyautogui.position()

    def get_screen_size(self) -> tuple[int, int]:
        """
        Get screen size.

        Returns:
            (width, height)
        """
        return pyautogui.size()

    def _resolve_target(
        self,
        target: tuple[int, int] | UIElement | BoundingBox | str,
    ) -> tuple[int, int]:
        """
        Resolve target position.

        Args:
            target: Can be a coordinate tuple, UIElement, BoundingBox, or "x,y" string.

        Returns:
            (x, y) coordinates.
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
            # Try parsing "x,y" format
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
        Move mouse to specified position.

        Args:
            x, y: Target coordinates.
            duration: Move duration in seconds; None uses config default.

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
        Move mouse by relative offset.

        Args:
            dx, dy: Relative offset.
            duration: Move duration.

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
        Click the mouse.

        Args:
            x, y: Click position; None means current position.
            button: Mouse button.
            clicks: Number of clicks.
            interval: Interval between multiple clicks.

        Returns:
            ActionResult
        """
        config = get_config().actions
        btn = button.value if isinstance(button, MouseButton) else button

        start_time = time.time()
        try:
            # Pre-click delay
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
        Click a target.

        Args:
            target: Target (coordinates, element, bounding box, or string).
            button: Mouse button.
            clicks: Number of clicks.

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
        Double-click.

        Args:
            x, y: Click position; None means current position.

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
        Right-click.

        Args:
            x, y: Click position; None means current position.

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
        Middle-click.

        Args:
            x, y: Click position; None means current position.

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
        Drag from one position to another.

        Args:
            start_x, start_y: Start position.
            end_x, end_y: End position.
            duration: Drag duration.
            button: Mouse button.

        Returns:
            ActionResult
        """
        btn = button.value if isinstance(button, MouseButton) else button

        start_time = time.time()
        try:
            # Move to start position first
            pyautogui.moveTo(start_x, start_y)
            # Drag to target position
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
        Drag from current position to target position.

        Args:
            end_x, end_y: Target position.
            duration: Drag duration.
            button: Mouse button.

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
        Scroll the mouse wheel.

        Args:
            clicks: Number of scroll clicks; positive scrolls up, negative scrolls down.
            x, y: Scroll position; None means current position.

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
        """Scroll up."""
        return self.scroll(abs(clicks), x, y)

    def scroll_down(
        self,
        clicks: int = 3,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """Scroll down."""
        return self.scroll(-abs(clicks), x, y)

    def hscroll(
        self,
        clicks: int,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """
        Horizontal scroll (if supported).

        Args:
            clicks: Number of scroll clicks; positive scrolls right, negative scrolls left.
            x, y: Scroll position.

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
        Press and hold a mouse button (without releasing).

        Args:
            x, y: Position; None means current position.
            button: Mouse button.

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
        Release a mouse button.

        Args:
            x, y: Position; None means current position.
            button: Mouse button.

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


# Global instance
_mouse: MouseController | None = None


def get_mouse() -> MouseController:
    """Get the global mouse controller."""
    global _mouse
    if _mouse is None:
        _mouse = MouseController()
    return _mouse
