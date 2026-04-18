"""
Windows desktop automation - UIAutomation elements

Wraps pywinauto control objects, providing a unified interface.
"""

import logging
import sys
from typing import Optional

from ..types import BoundingBox, UIElement, WindowInfo

# Platform check
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

try:
    from pywinauto.controls.uiawrapper import UIAWrapper
except ImportError:
    from openakita.tools._import_helper import import_or_hint

    raise ImportError(import_or_hint("pywinauto"))

logger = logging.getLogger(__name__)


class UIAElementWrapper:
    """
    UIAutomation element wrapper

    Wraps pywinauto's UIAWrapper, providing a friendlier interface.
    """

    def __init__(self, control: UIAWrapper):
        """
        Args:
            control: pywinauto UIAWrapper object
        """
        self._control = control

    @property
    def control(self) -> UIAWrapper:
        """Get the raw pywinauto control"""
        return self._control

    @property
    def name(self) -> str:
        """Get element name"""
        try:
            return self._control.element_info.name or ""
        except Exception:
            return ""

    @property
    def control_type(self) -> str:
        """Get control type"""
        try:
            return self._control.element_info.control_type or "Unknown"
        except Exception:
            return "Unknown"

    @property
    def automation_id(self) -> str:
        """Get automation ID"""
        try:
            return self._control.element_info.automation_id or ""
        except Exception:
            return ""

    @property
    def class_name(self) -> str:
        """Get class name"""
        try:
            return self._control.element_info.class_name or ""
        except Exception:
            return ""

    @property
    def handle(self) -> int:
        """Get window handle"""
        try:
            return self._control.element_info.handle or 0
        except Exception:
            return 0

    @property
    def process_id(self) -> int:
        """Get process ID"""
        try:
            return self._control.element_info.process_id or 0
        except Exception:
            return 0

    @property
    def bbox(self) -> BoundingBox | None:
        """Get bounding box"""
        try:
            rect = self._control.element_info.rectangle
            if rect:
                return BoundingBox(
                    left=rect.left,
                    top=rect.top,
                    right=rect.right,
                    bottom=rect.bottom,
                )
        except Exception:
            pass
        return None

    @property
    def center(self) -> tuple[int, int] | None:
        """Get center point coordinates"""
        bbox = self.bbox
        if bbox:
            return bbox.center
        return None

    @property
    def is_enabled(self) -> bool:
        """Whether the element is enabled"""
        try:
            return self._control.is_enabled()
        except Exception:
            return False

    @property
    def is_visible(self) -> bool:
        """Whether the element is visible"""
        try:
            return self._control.is_visible()
        except Exception:
            return False

    @property
    def is_focused(self) -> bool:
        """Whether the element has focus"""
        try:
            return self._control.has_keyboard_focus()
        except Exception:
            return False

    @property
    def value(self) -> str | None:
        """Get value (e.g., text input content)"""
        try:
            # Try different methods to get the value
            if hasattr(self._control, "get_value"):
                return self._control.get_value()
            if hasattr(self._control, "window_text"):
                return self._control.window_text()
            if hasattr(self._control, "texts"):
                texts = self._control.texts()
                if texts:
                    return texts[0] if len(texts) == 1 else str(texts)
        except Exception:
            pass
        return None

    def set_value(self, value: str) -> bool:
        """
        Set value

        Args:
            value: Value to set

        Returns:
            Whether the operation succeeded
        """
        try:
            if hasattr(self._control, "set_edit_text"):
                self._control.set_edit_text(value)
                return True
            if hasattr(self._control, "set_text"):
                self._control.set_text(value)
                return True
        except Exception as e:
            logger.error(f"Failed to set value: {e}")
        return False

    def click(self) -> bool:
        """
        Click the element

        Returns:
            Whether the operation succeeded
        """
        try:
            self._control.click_input()
            return True
        except Exception as e:
            logger.error(f"Failed to click element: {e}")
            return False

    def double_click(self) -> bool:
        """Double-click the element"""
        try:
            self._control.double_click_input()
            return True
        except Exception as e:
            logger.error(f"Failed to double click element: {e}")
            return False

    def right_click(self) -> bool:
        """Right-click the element"""
        try:
            self._control.right_click_input()
            return True
        except Exception as e:
            logger.error(f"Failed to right click element: {e}")
            return False

    def type_keys(self, keys: str, with_spaces: bool = True) -> bool:
        """
        Type a key sequence

        Args:
            keys: Key sequence
            with_spaces: Whether to include spaces

        Returns:
            Whether the operation succeeded
        """
        try:
            self._control.type_keys(keys, with_spaces=with_spaces)
            return True
        except Exception as e:
            logger.error(f"Failed to type keys: {e}")
            return False

    def set_focus(self) -> bool:
        """Set focus"""
        try:
            self._control.set_focus()
            return True
        except Exception as e:
            logger.error(f"Failed to set focus: {e}")
            return False

    def scroll(self, direction: str = "down", amount: int = 3) -> bool:
        """
        Scroll

        Args:
            direction: Direction (up, down, left, right)
            amount: Scroll amount

        Returns:
            Whether the operation succeeded
        """
        try:
            if direction == "down":
                self._control.scroll(direction="down", amount=amount)
            elif direction == "up":
                self._control.scroll(direction="up", amount=amount)
            return True
        except Exception as e:
            logger.error(f"Failed to scroll: {e}")
            return False

    def expand(self) -> bool:
        """Expand (e.g., tree node)"""
        try:
            if hasattr(self._control, "expand"):
                self._control.expand()
                return True
        except Exception as e:
            logger.error(f"Failed to expand: {e}")
        return False

    def collapse(self) -> bool:
        """Collapse (e.g., tree node)"""
        try:
            if hasattr(self._control, "collapse"):
                self._control.collapse()
                return True
        except Exception as e:
            logger.error(f"Failed to collapse: {e}")
        return False

    def select(self) -> bool:
        """Select (e.g., list item)"""
        try:
            if hasattr(self._control, "select"):
                self._control.select()
                return True
        except Exception as e:
            logger.error(f"Failed to select: {e}")
        return False

    def get_children(self) -> list["UIAElementWrapper"]:
        """Get child elements"""
        try:
            children = self._control.children()
            return [UIAElementWrapper(c) for c in children]
        except Exception as e:
            logger.error(f"Failed to get children: {e}")
            return []

    def get_parent(self) -> Optional["UIAElementWrapper"]:
        """Get parent element"""
        try:
            parent = self._control.parent()
            if parent:
                return UIAElementWrapper(parent)
        except Exception as e:
            logger.error(f"Failed to get parent: {e}")
        return None

    def find_child(
        self,
        name: str | None = None,
        control_type: str | None = None,
        automation_id: str | None = None,
        class_name: str | None = None,
    ) -> Optional["UIAElementWrapper"]:
        """
        Find a child element

        Args:
            name: Element name
            control_type: Control type
            automation_id: Automation ID
            class_name: Class name

        Returns:
            The found element, or None if not found
        """
        criteria = {}
        if name:
            criteria["title"] = name
        if control_type:
            criteria["control_type"] = control_type
        if automation_id:
            criteria["auto_id"] = automation_id
        if class_name:
            criteria["class_name"] = class_name

        if not criteria:
            return None

        try:
            child = self._control.child_window(**criteria)
            if child.exists():
                return UIAElementWrapper(child)
        except Exception as e:
            logger.debug(f"Child not found: {e}")

        return None

    def find_all_children(
        self,
        name: str | None = None,
        control_type: str | None = None,
        automation_id: str | None = None,
        class_name: str | None = None,
    ) -> list["UIAElementWrapper"]:
        """
        Find all matching child elements

        Args:
            name: Element name (supports regex)
            control_type: Control type
            automation_id: Automation ID
            class_name: Class name

        Returns:
            List of matching elements
        """
        criteria = {}
        if name:
            criteria["title_re"] = name
        if control_type:
            criteria["control_type"] = control_type
        if automation_id:
            criteria["auto_id"] = automation_id
        if class_name:
            criteria["class_name"] = class_name

        results = []
        try:
            if criteria:
                children = self._control.descendants(**criteria)
            else:
                children = self._control.descendants()

            for child in children:
                try:
                    results.append(UIAElementWrapper(child))
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Failed to find children: {e}")

        return results

    def to_ui_element(self) -> UIElement:
        """Convert to unified UIElement type"""
        return UIElement(
            name=self.name,
            control_type=self.control_type,
            bbox=self.bbox,
            automation_id=self.automation_id,
            class_name=self.class_name,
            value=self.value,
            is_enabled=self.is_enabled,
            is_visible=self.is_visible,
            is_focused=self.is_focused,
            source="uia",
            _control=self._control,
        )

    def to_window_info(self) -> WindowInfo:
        """Convert to WindowInfo (only applicable for window elements)"""
        bbox = self.bbox

        # Try to get process name
        process_name = ""
        try:
            import psutil

            pid = self.process_id
            if pid:
                proc = psutil.Process(pid)
                process_name = proc.name()
        except Exception:
            pass

        # Determine window state
        is_minimized = False
        is_maximized = False
        try:
            if hasattr(self._control, "is_minimized"):
                is_minimized = self._control.is_minimized()
            if hasattr(self._control, "is_maximized"):
                is_maximized = self._control.is_maximized()
        except Exception:
            pass

        return WindowInfo(
            title=self.name,
            handle=self.handle,
            class_name=self.class_name,
            process_id=self.process_id,
            process_name=process_name,
            bbox=bbox,
            is_visible=self.is_visible,
            is_minimized=is_minimized,
            is_maximized=is_maximized,
            is_focused=self.is_focused,
            _window=self._control,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "name": self.name,
            "control_type": self.control_type,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "bbox": self.bbox.to_tuple() if self.bbox else None,
            "center": self.center,
            "is_enabled": self.is_enabled,
            "is_visible": self.is_visible,
            "is_focused": self.is_focused,
            "value": self.value,
            "handle": self.handle,
            "process_id": self.process_id,
        }

    def __repr__(self) -> str:
        return (
            f"UIAElementWrapper("
            f"name={self.name!r}, "
            f"type={self.control_type!r}, "
            f"id={self.automation_id!r})"
        )


# Type alias
UIAElement = UIAElementWrapper
