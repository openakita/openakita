"""
Windows Desktop Automation - Keyboard Actions Module

Wraps PyAutoGUI keyboard operations.
"""

import logging
import sys
import time
from contextlib import contextmanager, suppress

from ..config import get_config
from ..types import ActionResult

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


# Common key alias mapping
KEY_ALIASES = {
    # Function keys
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "escape": "escape",
    "esc": "escape",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "ins": "insert",
    # Modifier keys
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "cmd": "win",
    "command": "win",
    # Arrow keys
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "home": "home",
    "end": "end",
    # F1-F12 function keys
    **{f"f{i}": f"f{i}" for i in range(1, 13)},
    # Other
    "printscreen": "printscreen",
    "prtsc": "printscreen",
    "scrolllock": "scrolllock",
    "pause": "pause",
    "capslock": "capslock",
    "numlock": "numlock",
}


class KeyboardController:
    """
    Keyboard Controller

    Wraps PyAutoGUI keyboard operations and provides a friendlier interface.
    """

    def __init__(self):
        self._configure_pyautogui()
        self._held_keys: list[str] = []  # Currently held keys

    def _configure_pyautogui(self) -> None:
        """Configure PyAutoGUI."""
        config = get_config().actions
        pyautogui.FAILSAFE = config.failsafe
        pyautogui.PAUSE = config.pause_between_actions

    def _normalize_key(self, key: str) -> str:
        """
        Normalize a key name.

        Args:
            key: Key name.

        Returns:
            Normalized key name.
        """
        key_lower = key.lower().strip()
        return KEY_ALIASES.get(key_lower, key_lower)

    def type_text(
        self,
        text: str,
        interval: float | None = None,
    ) -> ActionResult:
        """
        Type text.

        Supports Chinese and special characters (via clipboard).

        Args:
            text: The text to type.
            interval: Character interval; None uses the configured value.

        Returns:
            ActionResult
        """
        config = get_config().actions
        int_val = interval if interval is not None else config.type_interval

        start_time = time.time()
        try:
            # Check for non-ASCII characters
            if any(ord(c) > 127 for c in text):
                # Use clipboard-based input (supports Chinese)
                result = self._type_via_clipboard(text)
            else:
                # Direct ASCII input
                pyautogui.typewrite(text, interval=int_val)
                result = ActionResult(
                    success=True,
                    action="type",
                    target=text,
                    message=f"Typed {len(text)} characters",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            return result

        except Exception as e:
            logger.error(f"Failed to type text: {e}")
            return ActionResult(
                success=False,
                action="type",
                target=text,
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def _type_via_clipboard(self, text: str) -> ActionResult:
        """
        Type text via the clipboard (supports Chinese).

        Args:
            text: The text to type.

        Returns:
            ActionResult
        """
        import pyperclip

        start_time = time.time()
        try:
            # Save original clipboard content
            original_clipboard = ""
            with suppress(Exception):
                original_clipboard = pyperclip.paste()

            # Copy text to clipboard
            pyperclip.copy(text)

            # Paste
            pyautogui.hotkey("ctrl", "v")

            # Restore original clipboard content
            time.sleep(0.1)  # Wait for paste to complete
            with suppress(Exception):
                pyperclip.copy(original_clipboard)

            return ActionResult(
                success=True,
                action="type",
                target=text,
                message=f"Typed {len(text)} characters via clipboard",
                duration_ms=(time.time() - start_time) * 1000,
            )

        except ImportError:
            # If pyperclip is not available, try native Windows clipboard
            logger.warning("pyperclip not available, trying native Windows clipboard")
            return self._type_via_win_clipboard(text)
        except Exception as e:
            logger.error(f"Failed to type via clipboard: {e}")
            return ActionResult(
                success=False,
                action="type",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def _type_via_win_clipboard(self, text: str) -> ActionResult:
        """
        Type text using the native Windows clipboard.

        Args:
            text: The text to type.

        Returns:
            ActionResult
        """
        import ctypes

        start_time = time.time()
        try:
            # Windows API constants
            CF_UNICODETEXT = 13
            GHND = 0x0042

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # Open clipboard
            if not user32.OpenClipboard(None):
                raise Exception("Failed to open clipboard")

            try:
                # Empty clipboard
                user32.EmptyClipboard()

                # Prepare text data
                text_bytes = text.encode("utf-16-le") + b"\x00\x00"

                # Allocate global memory
                h_mem = kernel32.GlobalAlloc(GHND, len(text_bytes))
                if not h_mem:
                    raise Exception("Failed to allocate memory")

                # Lock memory and copy data
                p_mem = kernel32.GlobalLock(h_mem)
                if not p_mem:
                    kernel32.GlobalFree(h_mem)
                    raise Exception("Failed to lock memory")

                ctypes.memmove(p_mem, text_bytes, len(text_bytes))
                kernel32.GlobalUnlock(h_mem)

                # Set clipboard data
                user32.SetClipboardData(CF_UNICODETEXT, h_mem)

            finally:
                # Close clipboard
                user32.CloseClipboard()

            # Paste
            pyautogui.hotkey("ctrl", "v")

            return ActionResult(
                success=True,
                action="type",
                target=text,
                message=f"Typed {len(text)} characters via Windows clipboard",
                duration_ms=(time.time() - start_time) * 1000,
            )

        except Exception as e:
            logger.error(f"Failed to type via Windows clipboard: {e}")
            return ActionResult(
                success=False,
                action="type",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def press(self, key: str) -> ActionResult:
        """
        Press and release a key.

        Args:
            key: Key name.

        Returns:
            ActionResult
        """
        key = self._normalize_key(key)

        start_time = time.time()
        try:
            pyautogui.press(key)
            return ActionResult(
                success=True,
                action="press",
                target=key,
                message=f"Pressed {key}",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to press {key}: {e}")
            return ActionResult(
                success=False,
                action="press",
                target=key,
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def press_multiple(
        self,
        key: str,
        presses: int = 1,
        interval: float = 0.1,
    ) -> ActionResult:
        """
        Press a key multiple times.

        Args:
            key: Key name.
            presses: Number of presses.
            interval: Interval between presses.

        Returns:
            ActionResult
        """
        key = self._normalize_key(key)

        start_time = time.time()
        try:
            pyautogui.press(key, presses=presses, interval=interval)
            return ActionResult(
                success=True,
                action="press",
                target=key,
                message=f"Pressed {key} {presses} times",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to press {key}: {e}")
            return ActionResult(
                success=False,
                action="press",
                target=key,
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def hotkey(self, *keys: str) -> ActionResult:
        """
        Execute a keyboard shortcut / hotkey combination.

        Args:
            *keys: Key names, e.g. hotkey("ctrl", "c").

        Returns:
            ActionResult
        """
        normalized_keys = [self._normalize_key(k) for k in keys]
        key_combo = "+".join(normalized_keys)

        start_time = time.time()
        try:
            pyautogui.hotkey(*normalized_keys)
            return ActionResult(
                success=True,
                action="hotkey",
                target=key_combo,
                message=f"Pressed hotkey {key_combo}",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to press hotkey {key_combo}: {e}")
            return ActionResult(
                success=False,
                action="hotkey",
                target=key_combo,
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def key_down(self, key: str) -> ActionResult:
        """
        Press and hold a key (without releasing).

        Args:
            key: Key name.

        Returns:
            ActionResult
        """
        key = self._normalize_key(key)

        start_time = time.time()
        try:
            pyautogui.keyDown(key)
            self._held_keys.append(key)
            return ActionResult(
                success=True,
                action="key_down",
                target=key,
                message=f"Key {key} down",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to press down {key}: {e}")
            return ActionResult(
                success=False,
                action="key_down",
                target=key,
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def key_up(self, key: str) -> ActionResult:
        """
        Release a held key.

        Args:
            key: Key name.

        Returns:
            ActionResult
        """
        key = self._normalize_key(key)

        start_time = time.time()
        try:
            pyautogui.keyUp(key)
            if key in self._held_keys:
                self._held_keys.remove(key)
            return ActionResult(
                success=True,
                action="key_up",
                target=key,
                message=f"Key {key} up",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to release {key}: {e}")
            return ActionResult(
                success=False,
                action="key_up",
                target=key,
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    @contextmanager
    def hold(self, *keys: str):
        """
        Context manager for holding keys down.

        Usage:
            with keyboard.hold("ctrl", "shift"):
                keyboard.press("n")

        Args:
            *keys: Keys to hold down.
        """
        normalized_keys = [self._normalize_key(k) for k in keys]

        try:
            # Press all keys
            for key in normalized_keys:
                pyautogui.keyDown(key)
                self._held_keys.append(key)
            yield
        finally:
            # Release all keys (in reverse order)
            for key in reversed(normalized_keys):
                pyautogui.keyUp(key)
                if key in self._held_keys:
                    self._held_keys.remove(key)

    def release_all(self) -> ActionResult:
        """
        Release all held keys.

        Returns:
            ActionResult
        """
        start_time = time.time()
        released = []

        try:
            for key in self._held_keys[:]:  # Iterate over a copy
                pyautogui.keyUp(key)
                released.append(key)
                self._held_keys.remove(key)

            return ActionResult(
                success=True,
                action="release_all",
                message=f"Released keys: {released}" if released else "No keys to release",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.error(f"Failed to release keys: {e}")
            return ActionResult(
                success=False,
                action="release_all",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    # Convenience methods
    def copy(self) -> ActionResult:
        """Ctrl+C Copy"""
        return self.hotkey("ctrl", "c")

    def paste(self) -> ActionResult:
        """Ctrl+V Paste"""
        return self.hotkey("ctrl", "v")

    def cut(self) -> ActionResult:
        """Ctrl+X Cut"""
        return self.hotkey("ctrl", "x")

    def undo(self) -> ActionResult:
        """Ctrl+Z Undo"""
        return self.hotkey("ctrl", "z")

    def redo(self) -> ActionResult:
        """Ctrl+Y Redo"""
        return self.hotkey("ctrl", "y")

    def select_all(self) -> ActionResult:
        """Ctrl+A Select all"""
        return self.hotkey("ctrl", "a")

    def save(self) -> ActionResult:
        """Ctrl+S Save"""
        return self.hotkey("ctrl", "s")

    def find(self) -> ActionResult:
        """Ctrl+F Find"""
        return self.hotkey("ctrl", "f")

    def new(self) -> ActionResult:
        """Ctrl+N New"""
        return self.hotkey("ctrl", "n")

    def close_window(self) -> ActionResult:
        """Alt+F4 Close window"""
        return self.hotkey("alt", "f4")

    def switch_window(self) -> ActionResult:
        """Alt+Tab Switch window"""
        return self.hotkey("alt", "tab")

    def minimize_all(self) -> ActionResult:
        """Win+D Show desktop"""
        return self.hotkey("win", "d")

    def open_run(self) -> ActionResult:
        """Win+R Open Run dialog"""
        return self.hotkey("win", "r")

    def open_explorer(self) -> ActionResult:
        """Win+E Open File Explorer"""
        return self.hotkey("win", "e")

    def screenshot_to_clipboard(self) -> ActionResult:
        """Win+Shift+S Screenshot to clipboard"""
        return self.hotkey("win", "shift", "s")


# Global instance
_keyboard: KeyboardController | None = None


def get_keyboard() -> KeyboardController:
    """Get the global keyboard controller."""
    global _keyboard
    if _keyboard is None:
        _keyboard = KeyboardController()
    return _keyboard
