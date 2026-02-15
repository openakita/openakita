"""
Windows 桌面自动化 - UIAutomation 客户端

封装 pywinauto 的 Desktop 和 Application 类
"""

import logging
import re
import sys
import time
from typing import Any

from ..config import get_config
from ..types import WindowInfo
from .elements import UIAElementWrapper

# 平台检查
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
    UIAutomation 客户端

    提供桌面元素和窗口管理功能
    """

    def __init__(self, backend: str = "uia"):
        """
        Args:
            backend: pywinauto 后端，"uia" 或 "win32"
        """
        self._backend = backend
        self._desktop: Desktop | None = None

    @property
    def desktop(self) -> Desktop:
        """获取桌面对象（懒加载）"""
        if self._desktop is None:
            self._desktop = Desktop(backend=self._backend)
        return self._desktop

    def get_desktop_element(self) -> UIAElementWrapper:
        """
        获取桌面根元素

        Returns:
            桌面元素包装器
        """
        return UIAElementWrapper(self.desktop.window(class_name="Progman"))

    # ==================== 窗口管理 ====================

    def list_windows(
        self,
        visible_only: bool = True,
        with_title_only: bool = True,
    ) -> list[WindowInfo]:
        """
        列出所有顶层窗口

        Args:
            visible_only: 只返回可见窗口
            with_title_only: 只返回有标题的窗口

        Returns:
            窗口信息列表
        """
        windows = []

        try:
            for win in self.desktop.windows():
                try:
                    wrapper = UIAElementWrapper(win)

                    # 过滤条件
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
        查找窗口

        Args:
            title: 窗口标题（精确匹配）
            title_re: 窗口标题（正则匹配）
            class_name: 窗口类名
            process: 进程 ID
            handle: 窗口句柄
            timeout: 超时时间，None 使用配置

        Returns:
            找到的窗口，未找到返回 None
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
            # 使用 pywinauto 的等待机制
            win = self.desktop.window(**criteria)
            win.wait("exists", timeout=wait_timeout)
            return UIAElementWrapper(win)
        except (ElementNotFoundError, PywinautoTimeoutError) as e:
            logger.debug(f"Window not found: {criteria} - {e}")
            return None
        except ElementAmbiguousError:
            # 如果找到多个，返回第一个
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
        模糊查找窗口

        支持部分匹配标题

        Args:
            title_pattern: 标题模式（部分匹配）
            timeout: 超时时间

        Returns:
            找到的窗口
        """
        # 转换为正则表达式（忽略大小写，部分匹配）
        pattern = re.escape(title_pattern)
        return self.find_window(title_re=f".*{pattern}.*", timeout=timeout)

    def get_active_window(self) -> UIAElementWrapper | None:
        """
        获取当前活动窗口

        Returns:
            活动窗口，如果没有返回 None
        """
        try:
            # 方法1：使用 pywinauto
            import ctypes

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                app = Application(backend=self._backend).connect(handle=hwnd)
                win = app.window(handle=hwnd)
                return UIAElementWrapper(win)
        except Exception as e:
            logger.debug(f"Failed to get active window via handle: {e}")

        # 方法2：遍历窗口查找有焦点的
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
        激活窗口（设为前台）

        Args:
            window: 窗口元素

        Returns:
            是否成功
        """
        try:
            control = window.control

            # 如果窗口最小化，先恢复
            if hasattr(control, "is_minimized") and control.is_minimized():
                control.restore()

            # 设置为前台窗口
            control.set_focus()
            return True
        except Exception as e:
            logger.error(f"Failed to activate window: {e}")
            return False

    def minimize_window(self, window: UIAElementWrapper) -> bool:
        """最小化窗口"""
        try:
            window.control.minimize()
            return True
        except Exception as e:
            logger.error(f"Failed to minimize window: {e}")
            return False

    def maximize_window(self, window: UIAElementWrapper) -> bool:
        """最大化窗口"""
        try:
            window.control.maximize()
            return True
        except Exception as e:
            logger.error(f"Failed to maximize window: {e}")
            return False

    def restore_window(self, window: UIAElementWrapper) -> bool:
        """恢复窗口"""
        try:
            window.control.restore()
            return True
        except Exception as e:
            logger.error(f"Failed to restore window: {e}")
            return False

    def close_window(self, window: UIAElementWrapper) -> bool:
        """关闭窗口"""
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
        """移动窗口"""
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
        """调整窗口大小"""
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

    # ==================== 元素查找 ====================

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
        查找元素

        Args:
            root: 搜索根元素，None 表示在整个桌面搜索
            name: 元素名称（精确匹配）
            name_re: 元素名称（正则匹配）
            control_type: 控件类型
            automation_id: 自动化 ID
            class_name: 类名
            timeout: 超时时间

        Returns:
            找到的元素，未找到返回 None
        """
        config = get_config().uia
        wait_timeout = timeout if timeout is not None else config.timeout

        # 构建搜索条件
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

        # 确定搜索根
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
        查找所有匹配的元素

        Args:
            root: 搜索根元素
            name: 元素名称（精确匹配）
            name_re: 元素名称（正则匹配）
            control_type: 控件类型
            automation_id: 自动化 ID
            class_name: 类名
            depth: 搜索深度

        Returns:
            匹配的元素列表
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
        按路径查找元素

        Args:
            path: 路径列表，每个元素是搜索条件字典
            root: 搜索根元素

        Returns:
            找到的元素

        示例:
            find_element_by_path([
                {"control_type": "Window", "title": "记事本"},
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

    # ==================== 等待功能 ====================

    def wait_for_window(
        self,
        title: str | None = None,
        title_re: str | None = None,
        timeout: float = 10,
        interval: float = 0.5,
    ) -> UIAElementWrapper | None:
        """
        等待窗口出现

        Args:
            title: 窗口标题
            title_re: 窗口标题正则
            timeout: 超时时间
            interval: 检查间隔

        Returns:
            找到的窗口，超时返回 None
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
        等待窗口关闭

        Args:
            window: 窗口元素
            timeout: 超时时间
            interval: 检查间隔

        Returns:
            窗口是否已关闭
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
        等待元素出现

        Args:
            root: 搜索根元素
            name: 元素名称
            name_re: 元素名称正则
            control_type: 控件类型
            automation_id: 自动化 ID
            timeout: 超时时间
            interval: 检查间隔

        Returns:
            找到的元素，超时返回 None
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

    # ==================== 应用程序管理 ====================

    def start_application(
        self,
        path: str,
        args: str | None = None,
        work_dir: str | None = None,
        timeout: float = 10,
    ) -> UIAElementWrapper | None:
        """
        启动应用程序

        Args:
            path: 应用程序路径
            args: 命令行参数
            work_dir: 工作目录
            timeout: 等待窗口超时

        Returns:
            应用程序主窗口
        """
        try:
            app = Application(backend=self._backend).start(
                cmd_line=f"{path} {args}" if args else path,
                work_dir=work_dir,
                timeout=timeout,
            )

            # 等待主窗口
            time.sleep(0.5)

            try:
                # 尝试获取顶层窗口
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
        连接到已运行的应用程序

        Args:
            process: 进程 ID
            handle: 窗口句柄
            path: 可执行文件路径
            title: 窗口标题

        Returns:
            应用程序主窗口
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


# 全局实例
_uia_client: UIAClient | None = None


def get_uia_client() -> UIAClient:
    """获取全局 UIA 客户端"""
    global _uia_client
    if _uia_client is None:
        _uia_client = UIAClient()
    return _uia_client
