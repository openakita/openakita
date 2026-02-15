"""
Windows 桌面自动化 - 键盘操作模块

基于 PyAutoGUI 封装键盘操作
"""

import logging
import sys
import time
from contextlib import contextmanager, suppress

from ..config import get_config
from ..types import ActionResult

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


# 常用按键别名映射
KEY_ALIASES = {
    # 功能键
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
    # 修饰键
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "cmd": "win",
    "command": "win",
    # 方向键
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
    # 功能键 F1-F12
    **{f"f{i}": f"f{i}" for i in range(1, 13)},
    # 其他
    "printscreen": "printscreen",
    "prtsc": "printscreen",
    "scrolllock": "scrolllock",
    "pause": "pause",
    "capslock": "capslock",
    "numlock": "numlock",
}


class KeyboardController:
    """
    键盘控制器

    封装 PyAutoGUI 的键盘操作，提供更友好的接口
    """

    def __init__(self):
        self._configure_pyautogui()
        self._held_keys: list[str] = []  # 当前按住的键

    def _configure_pyautogui(self) -> None:
        """配置 PyAutoGUI"""
        config = get_config().actions
        pyautogui.FAILSAFE = config.failsafe
        pyautogui.PAUSE = config.pause_between_actions

    def _normalize_key(self, key: str) -> str:
        """
        标准化按键名称

        Args:
            key: 按键名称

        Returns:
            标准化后的按键名称
        """
        key_lower = key.lower().strip()
        return KEY_ALIASES.get(key_lower, key_lower)

    def type_text(
        self,
        text: str,
        interval: float | None = None,
    ) -> ActionResult:
        """
        输入文本

        支持中文和特殊字符（通过剪贴板方式）

        Args:
            text: 要输入的文本
            interval: 字符间隔，None 使用配置

        Returns:
            ActionResult
        """
        config = get_config().actions
        int_val = interval if interval is not None else config.type_interval

        start_time = time.time()
        try:
            # 检查是否包含非 ASCII 字符
            if any(ord(c) > 127 for c in text):
                # 使用剪贴板方式输入（支持中文）
                result = self._type_via_clipboard(text)
            else:
                # 直接输入 ASCII 字符
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
        通过剪贴板输入文本（支持中文）

        Args:
            text: 要输入的文本

        Returns:
            ActionResult
        """
        import pyperclip

        start_time = time.time()
        try:
            # 保存原有剪贴板内容
            original_clipboard = ""
            with suppress(Exception):
                original_clipboard = pyperclip.paste()

            # 复制文本到剪贴板
            pyperclip.copy(text)

            # 粘贴
            pyautogui.hotkey("ctrl", "v")

            # 恢复原有剪贴板内容
            time.sleep(0.1)  # 等待粘贴完成
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
            # 如果没有 pyperclip，尝试使用 Windows 原生方式
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
        使用 Windows 原生剪贴板输入文本

        Args:
            text: 要输入的文本

        Returns:
            ActionResult
        """
        import ctypes

        start_time = time.time()
        try:
            # Windows API 常量
            CF_UNICODETEXT = 13
            GHND = 0x0042

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # 打开剪贴板
            if not user32.OpenClipboard(None):
                raise Exception("Failed to open clipboard")

            try:
                # 清空剪贴板
                user32.EmptyClipboard()

                # 准备文本数据
                text_bytes = text.encode("utf-16-le") + b"\x00\x00"

                # 分配全局内存
                h_mem = kernel32.GlobalAlloc(GHND, len(text_bytes))
                if not h_mem:
                    raise Exception("Failed to allocate memory")

                # 锁定内存并复制数据
                p_mem = kernel32.GlobalLock(h_mem)
                if not p_mem:
                    kernel32.GlobalFree(h_mem)
                    raise Exception("Failed to lock memory")

                ctypes.memmove(p_mem, text_bytes, len(text_bytes))
                kernel32.GlobalUnlock(h_mem)

                # 设置剪贴板数据
                user32.SetClipboardData(CF_UNICODETEXT, h_mem)

            finally:
                # 关闭剪贴板
                user32.CloseClipboard()

            # 粘贴
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
        按下并释放按键

        Args:
            key: 按键名称

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
        多次按下按键

        Args:
            key: 按键名称
            presses: 按下次数
            interval: 按键间隔

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
        执行快捷键组合

        Args:
            *keys: 按键名称列表，如 hotkey("ctrl", "c")

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
        按下按键（不释放）

        Args:
            key: 按键名称

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
        释放按键

        Args:
            key: 按键名称

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
        按住按键的上下文管理器

        用法:
            with keyboard.hold("ctrl", "shift"):
                keyboard.press("n")

        Args:
            *keys: 要按住的按键
        """
        normalized_keys = [self._normalize_key(k) for k in keys]

        try:
            # 按下所有键
            for key in normalized_keys:
                pyautogui.keyDown(key)
                self._held_keys.append(key)
            yield
        finally:
            # 释放所有键（逆序）
            for key in reversed(normalized_keys):
                pyautogui.keyUp(key)
                if key in self._held_keys:
                    self._held_keys.remove(key)

    def release_all(self) -> ActionResult:
        """
        释放所有按住的按键

        Returns:
            ActionResult
        """
        start_time = time.time()
        released = []

        try:
            for key in self._held_keys[:]:  # 使用副本遍历
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

    # 便捷方法
    def copy(self) -> ActionResult:
        """Ctrl+C 复制"""
        return self.hotkey("ctrl", "c")

    def paste(self) -> ActionResult:
        """Ctrl+V 粘贴"""
        return self.hotkey("ctrl", "v")

    def cut(self) -> ActionResult:
        """Ctrl+X 剪切"""
        return self.hotkey("ctrl", "x")

    def undo(self) -> ActionResult:
        """Ctrl+Z 撤销"""
        return self.hotkey("ctrl", "z")

    def redo(self) -> ActionResult:
        """Ctrl+Y 重做"""
        return self.hotkey("ctrl", "y")

    def select_all(self) -> ActionResult:
        """Ctrl+A 全选"""
        return self.hotkey("ctrl", "a")

    def save(self) -> ActionResult:
        """Ctrl+S 保存"""
        return self.hotkey("ctrl", "s")

    def find(self) -> ActionResult:
        """Ctrl+F 查找"""
        return self.hotkey("ctrl", "f")

    def new(self) -> ActionResult:
        """Ctrl+N 新建"""
        return self.hotkey("ctrl", "n")

    def close_window(self) -> ActionResult:
        """Alt+F4 关闭窗口"""
        return self.hotkey("alt", "f4")

    def switch_window(self) -> ActionResult:
        """Alt+Tab 切换窗口"""
        return self.hotkey("alt", "tab")

    def minimize_all(self) -> ActionResult:
        """Win+D 显示桌面"""
        return self.hotkey("win", "d")

    def open_run(self) -> ActionResult:
        """Win+R 打开运行"""
        return self.hotkey("win", "r")

    def open_explorer(self) -> ActionResult:
        """Win+E 打开资源管理器"""
        return self.hotkey("win", "e")

    def screenshot_to_clipboard(self) -> ActionResult:
        """Win+Shift+S 截图到剪贴板"""
        return self.hotkey("win", "shift", "s")


# 全局实例
_keyboard: KeyboardController | None = None


def get_keyboard() -> KeyboardController:
    """获取全局键盘控制器"""
    global _keyboard
    if _keyboard is None:
        _keyboard = KeyboardController()
    return _keyboard
