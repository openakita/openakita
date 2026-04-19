"""
Windows Desktop Automation - Main Controller

Unified interface that intelligently selects UIA or Vision approach
"""

import asyncio
import logging
import sys
import time

from PIL import Image

from .actions import KeyboardController, MouseController, get_keyboard, get_mouse
from .cache import ElementCache, get_cache
from .capture import ScreenCapture, get_capture
from .config import DesktopConfig, get_config
from .types import (
    ActionResult,
    FindMethod,
    MouseButton,
    ScrollDirection,
    UIElement,
    WindowAction,
    WindowInfo,
)
from .uia import UIAClient, UIAElementWrapper, UIAInspector, get_uia_client
from .vision import VisionAnalyzer, get_vision_analyzer

# Platform check
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

logger = logging.getLogger(__name__)


class DesktopController:
    """
    Windows Desktop Controller

    Unified interface that intelligently selects UIA or Vision approach:
    - Standard Windows applications → UIAutomation (fast and accurate)
    - Non-standard UI → Vision (universal fallback)
    """

    def __init__(
        self,
        config: DesktopConfig | None = None,
    ):
        """
        Args:
            config: Configuration object; uses global config if None
        """
        self._config = config or get_config()

        # Lazy initialization of components
        self._capture: ScreenCapture | None = None
        self._mouse: MouseController | None = None
        self._keyboard: KeyboardController | None = None
        self._uia: UIAClient | None = None
        self._vision: VisionAnalyzer | None = None
        self._cache: ElementCache | None = None
        self._inspector: UIAInspector | None = None

    # ==================== Component Accessors ====================

    @property
    def capture(self) -> ScreenCapture:
        """Screenshot module"""
        if self._capture is None:
            self._capture = get_capture()
        return self._capture

    @property
    def mouse(self) -> MouseController:
        """Mouse controller"""
        if self._mouse is None:
            self._mouse = get_mouse()
        return self._mouse

    @property
    def keyboard(self) -> KeyboardController:
        """Keyboard controller"""
        if self._keyboard is None:
            self._keyboard = get_keyboard()
        return self._keyboard

    @property
    def uia(self) -> UIAClient:
        """UIAutomation client"""
        if self._uia is None:
            self._uia = get_uia_client()
        return self._uia

    @property
    def vision(self) -> VisionAnalyzer:
        """Vision analyzer"""
        if self._vision is None:
            self._vision = get_vision_analyzer()
        return self._vision

    @property
    def cache(self) -> ElementCache:
        """Element cache"""
        if self._cache is None:
            self._cache = get_cache()
        return self._cache

    @property
    def inspector(self) -> UIAInspector:
        """UIA inspector"""
        if self._inspector is None:
            self._inspector = UIAInspector(self.uia)
        return self._inspector

    # ==================== Screenshot ====================

    def screenshot(
        self,
        window_title: str | None = None,
        region: tuple[int, int, int, int] | None = None,
        monitor: int | None = None,
    ) -> Image.Image:
        """
        Take a screenshot

        Args:
            window_title: Window title, capture specified window
            region: Region (x, y, width, height)
            monitor: Display index

        Returns:
            PIL Image object
        """
        if window_title:
            # Find window and capture
            window = self.uia.find_window_fuzzy(window_title, timeout=2.0)
            if window and window.bbox:
                return self.capture.capture_window(window.bbox, window_title)
            logger.warning(f"Window not found: {window_title}, capturing full screen")

        return self.capture.capture(monitor=monitor, region=region)

    def screenshot_base64(
        self,
        window_title: str | None = None,
        region: tuple[int, int, int, int] | None = None,
        resize: bool = True,
    ) -> str:
        """Take screenshot and return base64"""
        img = self.screenshot(window_title=window_title, region=region)
        return self.capture.to_base64(img, resize_for_api=resize)

    # ==================== Element Finding ====================

    async def find_element(
        self,
        target: str,
        window_title: str | None = None,
        method: str | FindMethod = FindMethod.AUTO,
        timeout: float | None = None,
    ) -> UIElement | None:
        """
        Find a UI element

        Args:
            target: Element description (e.g. "Save button", "name:Save", "id:btn_save")
            window_title: Restrict search to a specific window
            method: Find method (auto, uia, vision)
            timeout: Timeout in seconds

        Returns:
            Found element, or None if not found
        """
        method = FindMethod(method) if isinstance(method, str) else method
        config = self._config.uia
        search_timeout = timeout or config.timeout

        # Parse target
        parsed = self._parse_target(target)

        # Get search root
        root = None
        if window_title:
            root = self.uia.find_window_fuzzy(window_title, timeout=2.0)
            if not root:
                logger.warning(f"Window not found: {window_title}")

        # Select strategy based on method
        if method == FindMethod.UIA:
            return await self._find_by_uia(parsed, root, search_timeout)
        elif method == FindMethod.VISION:
            return await self._find_by_vision(target, window_title)
        else:  # AUTO
            # Try UIA first
            element = await self._find_by_uia(parsed, root, search_timeout / 2)
            if element:
                return element

            # Fall back to Vision
            if self._config.vision.enabled:
                logger.info(f"UIA not found, falling back to vision: {target}")
                return await self._find_by_vision(target, window_title)

            return None

    def _parse_target(self, target: str) -> dict:
        """
        Parse target string

        Supported formats:
        - "name:Save" → find by name
        - "id:btn_save" → find by automation ID
        - "type:Button" → find by control type
        - "Save button" → natural language description
        """
        result = {"description": target}

        if ":" in target:
            prefix, value = target.split(":", 1)
            prefix = prefix.lower().strip()
            value = value.strip()

            if prefix == "name":
                result["name"] = value
            elif prefix == "id":
                result["automation_id"] = value
            elif prefix == "type":
                result["control_type"] = value
            elif prefix == "class":
                result["class_name"] = value

        return result

    async def _find_by_uia(
        self,
        criteria: dict,
        root: UIAElementWrapper | None,
        timeout: float,
    ) -> UIElement | None:
        """Find using UIAutomation"""
        try:
            element = self.uia.find_element(
                root=root,
                name=criteria.get("name"),
                name_re=criteria.get("description") if not criteria.get("name") else None,
                control_type=criteria.get("control_type"),
                automation_id=criteria.get("automation_id"),
                class_name=criteria.get("class_name"),
                timeout=timeout,
            )

            if element:
                return element.to_ui_element()
        except Exception as e:
            logger.debug(f"UIA search failed: {e}")

        return None

    async def _find_by_vision(
        self,
        description: str,
        window_title: str | None = None,
    ) -> UIElement | None:
        """Find using vision recognition"""
        try:
            # Take screenshot
            img = self.screenshot(window_title=window_title)

            # Vision search
            location = await self.vision.find_element(description, img)

            if location:
                return location.to_ui_element()
        except Exception as e:
            logger.error(f"Vision search failed: {e}")

        return None

    # ==================== Click Operations ====================

    async def click(
        self,
        target: str | tuple[int, int] | UIElement,
        button: str | MouseButton = MouseButton.LEFT,
        double: bool = False,
        method: str | FindMethod = FindMethod.AUTO,
    ) -> ActionResult:
        """
        Click on target

        Args:
            target: Target (element description, coordinate tuple, or UIElement)
            button: Mouse button
            double: Whether to double-click
            method: Element finding method

        Returns:
            ActionResult
        """
        start_time = time.time()

        try:
            # Resolve target coordinates
            x, y = await self._resolve_click_target(target, method)

            if x is None or y is None:
                return ActionResult(
                    success=False,
                    action="click",
                    target=str(target),
                    error=f"Cannot find target: {target}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # Execute click
            clicks = 2 if double else 1
            result = self.mouse.click(x, y, button=button, clicks=clicks)
            result.target = str(target)

            return result

        except Exception as e:
            logger.error(f"Click failed: {e}")
            return ActionResult(
                success=False,
                action="click",
                target=str(target),
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    async def _resolve_click_target(
        self,
        target: str | tuple[int, int] | UIElement,
        method: FindMethod,
    ) -> tuple[int | None, int | None]:
        """Resolve click target to coordinates"""
        if isinstance(target, tuple) and len(target) == 2:
            return target

        if isinstance(target, UIElement):
            if target.center:
                return target.center
            return None, None

        if isinstance(target, str):
            # Try parsing coordinate string "x,y"
            try:
                parts = target.split(",")
                if len(parts) == 2:
                    return int(parts[0].strip()), int(parts[1].strip())
            except (ValueError, IndexError):
                pass

            # Find as element description
            element = await self.find_element(target, method=method)
            if element and element.center:
                return element.center

        return None, None

    async def double_click(
        self,
        target: str | tuple[int, int] | UIElement,
        method: str | FindMethod = FindMethod.AUTO,
    ) -> ActionResult:
        """Double-click"""
        return await self.click(target, double=True, method=method)

    async def right_click(
        self,
        target: str | tuple[int, int] | UIElement,
        method: str | FindMethod = FindMethod.AUTO,
    ) -> ActionResult:
        """Right-click"""
        return await self.click(target, button=MouseButton.RIGHT, method=method)

    # ==================== Input Operations ====================

    def type_text(
        self,
        text: str,
        clear_first: bool = False,
    ) -> ActionResult:
        """
        Type text

        Args:
            text: Text to type
            clear_first: Whether to clear first (Ctrl+A)

        Returns:
            ActionResult
        """
        start_time = time.time()

        try:
            if clear_first:
                self.keyboard.select_all()
                time.sleep(0.1)

            result = self.keyboard.type_text(text)
            return result

        except Exception as e:
            return ActionResult(
                success=False,
                action="type",
                target=text,
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def hotkey(self, *keys: str) -> ActionResult:
        """Execute hotkey"""
        return self.keyboard.hotkey(*keys)

    def press(self, key: str) -> ActionResult:
        """Press a key"""
        return self.keyboard.press(key)

    # ==================== Scroll Operations ====================

    def scroll(
        self,
        direction: str | ScrollDirection,
        amount: int = 3,
        x: int | None = None,
        y: int | None = None,
    ) -> ActionResult:
        """
        Scroll

        Args:
            direction: Direction (up, down, left, right)
            amount: Scroll amount
            x, y: Scroll position

        Returns:
            ActionResult
        """
        direction = ScrollDirection(direction) if isinstance(direction, str) else direction

        if direction == ScrollDirection.UP:
            return self.mouse.scroll_up(amount, x, y)
        elif direction == ScrollDirection.DOWN:
            return self.mouse.scroll_down(amount, x, y)
        elif direction in (ScrollDirection.LEFT, ScrollDirection.RIGHT):
            clicks = -amount if direction == ScrollDirection.LEFT else amount
            return self.mouse.hscroll(clicks, x, y)

        return ActionResult(success=False, action="scroll", error="Invalid direction")

    # ==================== Window Management ====================

    def list_windows(
        self,
        visible_only: bool = True,
    ) -> list[WindowInfo]:
        """List all windows"""
        return self.uia.list_windows(visible_only=visible_only)

    def get_active_window(self) -> WindowInfo | None:
        """Get current active window"""
        window = self.uia.get_active_window()
        if window:
            return window.to_window_info()
        return None

    def switch_to_window(self, title: str) -> ActionResult:
        """
        Switch to the specified window

        Args:
            title: Window title (fuzzy match)

        Returns:
            ActionResult
        """
        start_time = time.time()

        window = self.uia.find_window_fuzzy(title, timeout=3.0)
        if not window:
            return ActionResult(
                success=False,
                action="switch_window",
                target=title,
                error=f"Window not found: {title}",
                duration_ms=(time.time() - start_time) * 1000,
            )

        success = self.uia.activate_window(window)
        return ActionResult(
            success=success,
            action="switch_window",
            target=title,
            message=f"Switched to window: {window.name}" if success else "",
            error="" if success else "Failed to activate window",
            duration_ms=(time.time() - start_time) * 1000,
        )

    def window_action(
        self,
        action: str | WindowAction,
        title: str | None = None,
    ) -> ActionResult:
        """
        Window action

        Args:
            action: Action type
            title: Window title

        Returns:
            ActionResult
        """
        action = WindowAction(action) if isinstance(action, str) else action
        start_time = time.time()

        if action == WindowAction.LIST:
            windows = self.list_windows()
            return ActionResult(
                success=True,
                action="list_windows",
                message=f"Found {len(windows)} windows",
                duration_ms=(time.time() - start_time) * 1000,
            )

        # Other actions require a window title
        if not title:
            return ActionResult(
                success=False,
                action=action.value,
                error="Window title required",
            )

        window = self.uia.find_window_fuzzy(title, timeout=3.0)
        if not window:
            return ActionResult(
                success=False,
                action=action.value,
                target=title,
                error=f"Window not found: {title}",
                duration_ms=(time.time() - start_time) * 1000,
            )

        success = False
        if action == WindowAction.SWITCH:
            success = self.uia.activate_window(window)
        elif action == WindowAction.MINIMIZE:
            success = self.uia.minimize_window(window)
        elif action == WindowAction.MAXIMIZE:
            success = self.uia.maximize_window(window)
        elif action == WindowAction.RESTORE:
            success = self.uia.restore_window(window)
        elif action == WindowAction.CLOSE:
            success = self.uia.close_window(window)

        return ActionResult(
            success=success,
            action=action.value,
            target=title,
            message=f"{action.value} window: {window.name}" if success else "",
            error="" if success else f"Failed to {action.value} window",
            duration_ms=(time.time() - start_time) * 1000,
        )

    # ==================== Wait Functions ====================

    async def wait_for_element(
        self,
        target: str,
        timeout: float = 10,
        interval: float = 0.5,
        method: str | FindMethod = FindMethod.AUTO,
    ) -> UIElement | None:
        """
        Wait for element to appear

        Args:
            target: Element description
            timeout: Timeout in seconds
            interval: Check interval
            method: Find method

        Returns:
            Found element, or None on timeout
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            element = await self.find_element(target, method=method, timeout=interval)
            if element:
                return element
            await asyncio.sleep(interval)

        return None

    async def wait_for_window(
        self,
        title: str,
        timeout: float = 10,
        interval: float = 0.5,
    ) -> bool:
        """
        Wait for window to appear

        Args:
            title: Window title
            timeout: Timeout in seconds
            interval: Check interval

        Returns:
            Whether the window was found
        """
        window = self.uia.wait_for_window(
            title_re=f".*{title}.*",
            timeout=timeout,
            interval=interval,
        )
        return window is not None

    # ==================== Inspection Functions ====================

    def inspect(
        self,
        window_title: str | None = None,
        depth: int = 2,
    ) -> dict:
        """
        Inspect the UI element tree of a window

        Args:
            window_title: Window title; uses active window if None
            depth: Traversal depth

        Returns:
            Element tree dict
        """
        root = None
        if window_title:
            root = self.uia.find_window_fuzzy(window_title, timeout=2.0)

        return self.inspector.get_element_tree(root, depth=depth)

    def inspect_text(
        self,
        window_title: str | None = None,
        depth: int = 2,
    ) -> str:
        """
        Inspect the UI element tree of a window (text format)

        Args:
            window_title: Window title
            depth: Traversal depth

        Returns:
            Formatted tree text
        """
        root = None
        if window_title:
            root = self.uia.find_window_fuzzy(window_title, timeout=2.0)

        return self.inspector.print_element_tree(root, depth=depth)

    # ==================== Visual Analysis ====================

    async def analyze_screen(
        self,
        window_title: str | None = None,
        query: str | None = None,
    ) -> dict:
        """
        Analyze screen content

        Args:
            window_title: Window title
            query: Custom query; performs general analysis if None

        Returns:
            Analysis result
        """
        img = self.screenshot(window_title=window_title)

        if query:
            result = await self.vision.answer_question(query, img)
        else:
            result = await self.vision.analyze_page(img)

        return {
            "success": result.success,
            "answer": result.answer,
            "elements": [
                {
                    "description": e.description,
                    "center": e.center,
                    "bbox": e.bbox.to_tuple(),
                }
                for e in result.elements
            ],
            "error": result.error,
        }


# Global instance
_controller: DesktopController | None = None


def get_controller() -> DesktopController:
    """Get global controller"""
    global _controller
    if _controller is None:
        _controller = DesktopController()
    return _controller
