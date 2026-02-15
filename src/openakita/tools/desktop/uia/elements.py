"""
Windows 桌面自动化 - UIAutomation 元素

封装 pywinauto 的控件对象，提供统一的接口
"""

import logging
import sys
from typing import Optional

from ..types import BoundingBox, UIElement, WindowInfo

# 平台检查
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
    UIAutomation 元素包装器

    封装 pywinauto 的 UIAWrapper，提供更友好的接口
    """

    def __init__(self, control: UIAWrapper):
        """
        Args:
            control: pywinauto UIAWrapper 对象
        """
        self._control = control

    @property
    def control(self) -> UIAWrapper:
        """获取原始 pywinauto 控件"""
        return self._control

    @property
    def name(self) -> str:
        """获取元素名称"""
        try:
            return self._control.element_info.name or ""
        except Exception:
            return ""

    @property
    def control_type(self) -> str:
        """获取控件类型"""
        try:
            return self._control.element_info.control_type or "Unknown"
        except Exception:
            return "Unknown"

    @property
    def automation_id(self) -> str:
        """获取自动化 ID"""
        try:
            return self._control.element_info.automation_id or ""
        except Exception:
            return ""

    @property
    def class_name(self) -> str:
        """获取类名"""
        try:
            return self._control.element_info.class_name or ""
        except Exception:
            return ""

    @property
    def handle(self) -> int:
        """获取窗口句柄"""
        try:
            return self._control.element_info.handle or 0
        except Exception:
            return 0

    @property
    def process_id(self) -> int:
        """获取进程 ID"""
        try:
            return self._control.element_info.process_id or 0
        except Exception:
            return 0

    @property
    def bbox(self) -> BoundingBox | None:
        """获取边界框"""
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
        """获取中心点坐标"""
        bbox = self.bbox
        if bbox:
            return bbox.center
        return None

    @property
    def is_enabled(self) -> bool:
        """是否可用"""
        try:
            return self._control.is_enabled()
        except Exception:
            return False

    @property
    def is_visible(self) -> bool:
        """是否可见"""
        try:
            return self._control.is_visible()
        except Exception:
            return False

    @property
    def is_focused(self) -> bool:
        """是否有焦点"""
        try:
            return self._control.has_keyboard_focus()
        except Exception:
            return False

    @property
    def value(self) -> str | None:
        """获取值（如输入框内容）"""
        try:
            # 尝试不同的方式获取值
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
        设置值

        Args:
            value: 要设置的值

        Returns:
            是否成功
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
        点击元素

        Returns:
            是否成功
        """
        try:
            self._control.click_input()
            return True
        except Exception as e:
            logger.error(f"Failed to click element: {e}")
            return False

    def double_click(self) -> bool:
        """双击元素"""
        try:
            self._control.double_click_input()
            return True
        except Exception as e:
            logger.error(f"Failed to double click element: {e}")
            return False

    def right_click(self) -> bool:
        """右键点击元素"""
        try:
            self._control.right_click_input()
            return True
        except Exception as e:
            logger.error(f"Failed to right click element: {e}")
            return False

    def type_keys(self, keys: str, with_spaces: bool = True) -> bool:
        """
        输入按键序列

        Args:
            keys: 按键序列
            with_spaces: 是否包含空格

        Returns:
            是否成功
        """
        try:
            self._control.type_keys(keys, with_spaces=with_spaces)
            return True
        except Exception as e:
            logger.error(f"Failed to type keys: {e}")
            return False

    def set_focus(self) -> bool:
        """设置焦点"""
        try:
            self._control.set_focus()
            return True
        except Exception as e:
            logger.error(f"Failed to set focus: {e}")
            return False

    def scroll(self, direction: str = "down", amount: int = 3) -> bool:
        """
        滚动

        Args:
            direction: 方向 (up, down, left, right)
            amount: 滚动量

        Returns:
            是否成功
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
        """展开（如树节点）"""
        try:
            if hasattr(self._control, "expand"):
                self._control.expand()
                return True
        except Exception as e:
            logger.error(f"Failed to expand: {e}")
        return False

    def collapse(self) -> bool:
        """折叠（如树节点）"""
        try:
            if hasattr(self._control, "collapse"):
                self._control.collapse()
                return True
        except Exception as e:
            logger.error(f"Failed to collapse: {e}")
        return False

    def select(self) -> bool:
        """选择（如列表项）"""
        try:
            if hasattr(self._control, "select"):
                self._control.select()
                return True
        except Exception as e:
            logger.error(f"Failed to select: {e}")
        return False

    def get_children(self) -> list["UIAElementWrapper"]:
        """获取子元素"""
        try:
            children = self._control.children()
            return [UIAElementWrapper(c) for c in children]
        except Exception as e:
            logger.error(f"Failed to get children: {e}")
            return []

    def get_parent(self) -> Optional["UIAElementWrapper"]:
        """获取父元素"""
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
        查找子元素

        Args:
            name: 元素名称
            control_type: 控件类型
            automation_id: 自动化 ID
            class_name: 类名

        Returns:
            找到的元素，未找到返回 None
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
        查找所有匹配的子元素

        Args:
            name: 元素名称（支持正则）
            control_type: 控件类型
            automation_id: 自动化 ID
            class_name: 类名

        Returns:
            匹配的元素列表
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
        """转换为统一的 UIElement 类型"""
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
        """转换为 WindowInfo（仅适用于窗口元素）"""
        bbox = self.bbox

        # 尝试获取进程名
        process_name = ""
        try:
            import psutil

            pid = self.process_id
            if pid:
                proc = psutil.Process(pid)
                process_name = proc.name()
        except Exception:
            pass

        # 判断窗口状态
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
        """转换为字典"""
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


# 类型别名
UIAElement = UIAElementWrapper
