"""
Windows desktop automation - UIAutomation client.

Wraps pywinauto's Desktop and Application classes.
"""

import logging
import re
import sys
import time
from typing import Any

from ..config import get_config
from ..types import WindowInfo
from .elements import UIAElementWrapper

# Platform check
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

try:
    from pywinauto import Application, Desktop
    from pywinauto.findwindows import ElementAmbiguousError, ElementNotFoundError
    from pywinauto.timings import TimeoutError as PywinautoTimeoutError
except ImportError:
    from openakita.tools._import_helper import import_or_hint

    raise ImportError(import_or_hint("pywinauto"))

logger = logging.getLogger(__name__)


class UIAClient:
    """
    UIAutomation client.

    Provides desktop element and window management functionality.
    """

    def __init__(self, backend: str = "uia"):
        """
        Args:
            backend: pywinauto backend, "uia" or "win32"
        """
        self._backend = backend
        self._desktop: Desktop | None = None

    @property
    def desktop(self) -> Desktop:
        """Get the desktop object (lazy-loaded)."""
        if self._desktop is None:
            self._desktop = Desktop(backend=self._backend)
        return self._desktop

    def get_desktop_element(self) -> UIAElementWrapper:
        """
        Get the desktop root element.

        Returns:
            Desktop element wrapper
        """
        return UIAElementWrapper(self.desktop.window(class_name="Progman"))

    # ==================== Window management ====================

    def list_windows(
        self,
        visible_only: bool = True,
        with_title_only: bool = True,
    ) -> list[WindowInfo]:
        """
        List all top-level windows.

        Args:
            visible_only: return only visible windows
            with_title_only: return only windows that have a title

        Returns:
            List of window info
        """
        windows = []

        try:
            for win in self.desktop.windows():
                try:
                    wrapper = UIAElementWrapper(win)

                    # Filter conditions
                    if visible_only and not wrapper.is_visible:
                        continue
                    if with_title_only and not wrapper.name:
                        continue

                    windows.append(wrapper.to_window_info())
                except Exception as e:
                    logger.debug(f"Failed to get window info: {e}")
                    continue
        except Exception as e:
            logger.error(f"Failed to list windows: {e}")

        return windows

    def find_window(
        self,
        title: str | None = None,
        title_re: str | None = None,
        class_name: str | None = None,
        process: int | None = None,
        handle: int | None = None,
        timeout: float | None = None,
    ) -> UIAElementWrapper | None:
        """
        Find a window.

        Args:
            title: window title (exact match)
            title_re: window title (regex match)
            class_name: window class name
            process: process ID
            handle: window handle
            timeout: timeout; None uses the config value

        Returns:
            The found window, or None if not found
        """
        config = get_config().uia
        wait_timeout = timeout if timeout is not None else config.timeout

        criteria: dict[str, Any] = {}
        if title:
            criteria["title"] = title
        if title_re:
            criteria["title_re"] = title_re
        if class_name:
            criteria["class_name"] = class_name
        if process:
            criteria["process"] = process
        if handle:
            criteria["handle"] = handle

        if not criteria:
            logger.warning("No search criteria provided for find_window")
            return None

        try:
            # Use pywinauto's wait mechanism
            win = self.desktop.window(**criteria)
            win.wait("exists", timeout=wait_timeout)
            return UIAElementWrapper(win)
        except (ElementNotFoundError, PywinautoTimeoutError) as e:
            logger.debug(f"Window not found: {criteria} - {e}")
            return None
        except ElementAmbiguousError:
            # If multiple matches are found, return the first one
            logger.warning(f"Multiple windows match criteria: {criteria}")
            try:
                wins = self.desktop.windows(**criteria)
                if wins:
                    return UIAElementWrapper(wins[0])
            except Exception:
                pass
            return None
        except Exception as e:
            logger.error(f"Error finding window: {e}")
            return None

    def find_window_fuzzy(
        self,
        title_pattern: str,
        timeout: float | None = None,
    ) -> UIAElementWrapper | None:
        """
        Fuzzy window lookup.

        Supports partial title matching.

        Args:
            title_pattern: title pattern (partial match)
            timeout: timeout

        Returns:
            The found window
        """
        # Convert to a regex (case-insensitive, partial match)
        pattern = re.escape(title_pattern)
        return self.find_window(title_re=f".*{pattern}.*", timeout=timeout)

    def get_active_window(self) -> UIAElementWrapper | None:
        """
        Get the currently active window.

        Returns:
            The active window, or None if there is none
        """
        try:
            # Method 1: use pywinauto
            import ctypes

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                app = Application(backend=self._backend).connect(handle=hwnd)
                win = app.window(handle=hwnd)
                return UIAElementWrapper(win)
        except Exception as e:
            logger.debug(f"Failed to get active window via handle: {e}")

        # Method 2: iterate windows to find the one with focus
        try:
            for win in self.desktop.windows():
                try:
                    wrapper = UIAElementWrapper(win)
                    if wrapper.is_focused:
                        return wrapper
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Failed to get active window: {e}")

        return None

    def activate_window(self, window: UIAElementWrapper) -> bool:
        """
        Activate a window (bring to foreground).

        Args:
            window: window element

        Returns:
            Whether successful
        """
        try:
            control = window.control

            # If the window is minimized, restore it first
            if hasattr(control, "is_minimized") and control.is_minimized():
                control.restore()

            # Set as foreground window
            control.set_focus()
            return True
        except Exception as e:
            logger.error(f"Failed to activate window: {e}")
            return False

    def minimize_window(self, window: UIAElementWrapper) -> bool:
        """Minimize window."""
        try:
            window.control.minimize()
            return True
        except Exception as e:
            logger.error(f"Failed to minimize window: {e}")
            return False

    def maximize_window(self, window: UIAElementWrapper) -> bool:
        """Maximize window."""
        try:
            window.control.maximize()
            return True
        except Exception as e:
            logger.error(f"Failed to maximize window: {e}")
            return False

    def restore_window(self, window: UIAElementWrapper) -> bool:
        """Restore window."""
        try:
            window.control.restore()
            return True
        except Exception as e:
            logger.error(f"Failed to restore window: {e}")
            return False

    def close_window(self, window: UIAElementWrapper) -> bool:
        """Close window."""
        try:
            window.control.close()
            return True
        except Exception as e:
            logger.error(f"Failed to close window: {e}")
            return False

    def move_window(
        self,
        window: UIAElementWrapper,
        x: int,
        y: int,
    ) -> bool:
        """Move window."""
        try:
            window.control.move_window(x, y)
            return True
        except Exception as e:
            logger.error(f"Failed to move window: {e}")
            return False

    def resize_window(
        self,
        window: UIAElementWrapper,
        width: int,
        height: int,
    ) -> bool:
        """Resize window."""
        try:
            bbox = window.bbox
            if bbox:
                window.control.move_window(
                    bbox.left,
                    bbox.top,
                    width,
                    height,
                )
                return True
        except Exception as e:
            logger.error(f"Failed to resize window: {e}")
        return False

    # ==================== Element lookup ====================

    def find_element(
        self,
        root: UIAElementWrapper | None = None,
        name: str | None = None,
        name_re: str | None = None,
        control_type: str | None = None,
        automation_id: str | None = None,
        class_name: str | None = None,
        timeout: float | None = None,
    ) -> UIAElementWrapper | None:
        """
        Find an element.

        Args:
            root: search root element; None searches the entire desktop
            name: element name (exact match)
            name_re: element name (regex match)
            control_type: control type
            automation_id: automation ID
            class_name: class name
            timeout: timeout

        Returns:
            The found element, or None if not found
        """
        config = get_config().uia
        wait_timeout = timeout if timeout is not None else config.timeout

        # Build search criteria
        criteria: dict[str, Any] = {}
        if name:
            criteria["title"] = name
        if name_re:
            criteria["title_re"] = name_re
        if control_type:
            criteria["control_type"] = control_type
        if automation_id:
            criteria["auto_id"] = automation_id
        if class_name:
            criteria["class_name"] = class_name

        if not criteria:
            logger.warning("No search criteria provided for find_element")
            return None

        # Determine the search root
        search_root = root.control if root else self.desktop

        try:
            elem = search_root.child_window(**criteria) if root else search_root.window(**criteria)

            elem.wait("exists", timeout=wait_timeout)
            return UIAElementWrapper(elem)
        except (ElementNotFoundError, PywinautoTimeoutError):
            logger.debug(f"Element not found: {criteria}")
            return None
        except Exception as e:
            logger.error(f"Error finding element: {e}")
            return None

    def find_all_elements(
        self,
        root: UIAElementWrapper | None = None,
        name: str | None = None,
        name_re: str | None = None,
        control_type: str | None = None,
        automation_id: str | None = None,
        class_name: str | None = None,
        depth: int = 10,
    ) -> list[UIAElementWrapper]:
        """
        Find all matching elements.

        Args:
            root: search root element
            name: element name (exact match)
            name_re: element name (regex match)
            control_type: control type
            automation_id: automation ID
            class_name: class name
            depth: search depth

        Returns:
            List of matching elements
        """
        criteria: dict[str, Any] = {"depth": depth}
        if name:
            criteria["title"] = name
        if name_re:
            criteria["title_re"] = name_re
        if control_type:
            criteria["control_type"] = control_type
        if automation_id:
            criteria["auto_id"] = automation_id
        if class_name:
            criteria["class_name"] = class_name

        search_root = root.control if root else self.desktop

        results = []
        try:
            if root:
                elements = search_root.descendants(**criteria)
            else:
                elements = search_root.windows(
                    **{k: v for k, v in criteria.items() if k != "depth"}
                )

            for elem in elements:
                try:
                    results.append(UIAElementWrapper(elem))
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Error finding elements: {e}")

        return results

    def find_element_by_path(
        self,
        path: list[dict[str, Any]],
        root: UIAElementWrapper | None = None,
    ) -> UIAElementWrapper | None:
        """
        Find an element by path.

        Args:
            path: list of steps, each a dict of search criteria
            root: search root element

        Returns:
            The found element

        Example:
            find_element_by_path([
                {"control_type": "Window", "title": "Notepad"},
                {"control_type": "Edit"},
            ])
        """
        current = root

        for criteria in path:
            found = current.find_child(**criteria) if current else self.find_element(**criteria)

            if not found:
                return None
            current = found

        return current

    # ==================== Waiting ====================

    def wait_for_window(
        self,
        title: str | None = None,
        title_re: str | None = None,
        timeout: float = 10,
        interval: float = 0.5,
    ) -> UIAElementWrapper | None:
        """
        Wait for a window to appear.

        Args:
            title: window title
            title_re: window title regex
            timeout: timeout
            interval: check interval

        Returns:
            The found window, or None on timeout
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            window = self.find_window(
                title=title,
                title_re=title_re,
                timeout=interval,
            )
            if window:
                return window
            time.sleep(interval)

        return None

    def wait_for_window_close(
        self,
        window: UIAElementWrapper,
        timeout: float = 10,
        interval: float = 0.5,
    ) -> bool:
        """
        Wait for a window to close.

        Args:
            window: window element
            timeout: timeout
            interval: check interval

        Returns:
            Whether the window has closed
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                if not window.control.exists():
                    return True
            except Exception:
                return True
            time.sleep(interval)

        return False

    def wait_for_element(
        self,
        root: UIAElementWrapper | None = None,
        name: str | None = None,
        name_re: str | None = None,
        control_type: str | None = None,
        automation_id: str | None = None,
        timeout: float = 10,
        interval: float = 0.5,
    ) -> UIAElementWrapper | None:
        """
        Wait for an element to appear.

        Args:
            root: search root element
            name: element name
            name_re: element name regex
            control_type: control type
            automation_id: automation ID
            timeout: timeout
            interval: check interval

        Returns:
            The found element, or None on timeout
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            element = self.find_element(
                root=root,
                name=name,
                name_re=name_re,
                control_type=control_type,
                automation_id=automation_id,
                timeout=interval,
            )
            if element:
                return element
            time.sleep(interval)

        return None

    # ==================== Application management ====================

    def start_application(
        self,
        path: str,
        args: str | None = None,
        work_dir: str | None = None,
        timeout: float = 10,
    ) -> UIAElementWrapper | None:
        """
        Start an application.

        Args:
            path: application path
            args: command-line arguments
            work_dir: working directory
            timeout: window-wait timeout

        Returns:
            Application main window
        """
        try:
            app = Application(backend=self._backend).start(
                cmd_line=f"{path} {args}" if args else path,
                work_dir=work_dir,
                timeout=timeout,
            )

            # Wait for the main window
            time.sleep(0.5)

            try:
                # Try to get the top-level window
                win = app.top_window()
                win.wait("ready", timeout=timeout)
                return UIAElementWrapper(win)
            except Exception:
                pass

            return None

        except Exception as e:
            logger.error(f"Failed to start application: {e}")
            return None

    def connect_to_application(
        self,
        process: int | None = None,
        handle: int | None = None,
        path: str | None = None,
        title: str | None = None,
    ) -> UIAElementWrapper | None:
        """
        Connect to a running application.

        Args:
            process: process ID
            handle: window handle
            path: executable path
            title: window title

        Returns:
            Application main window
        """
        try:
            connect_args = {}
            if process:
                connect_args["process"] = process
            if handle:
                connect_args["handle"] = handle
            if path:
                connect_args["path"] = path
            if title:
                connect_args["title"] = title

            if not connect_args:
                return None

            app = Application(backend=self._backend).connect(**connect_args)
            win = app.top_window()
            return UIAElementWrapper(win)

        except Exception as e:
            logger.error(f"Failed to connect to application: {e}")
            return None


# Global instance
_uia_client: UIAClient | None = None


def get_uia_client() -> UIAClient:
    """Get the global UIA client."""
    global _uia_client
    if _uia_client is None:
        _uia_client = UIAClient()
    return _uia_client
