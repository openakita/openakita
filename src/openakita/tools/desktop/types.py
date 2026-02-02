"""
Windows 桌面自动化 - 数据类型定义

定义所有模块共用的数据结构
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from datetime import datetime


class ControlType(str, Enum):
    """Windows UI 控件类型"""
    BUTTON = "Button"
    EDIT = "Edit"
    TEXT = "Text"
    CHECKBOX = "CheckBox"
    RADIOBUTTON = "RadioButton"
    COMBOBOX = "ComboBox"
    LISTBOX = "ListBox"
    LIST = "List"
    LISTITEM = "ListItem"
    MENU = "Menu"
    MENUITEM = "MenuItem"
    MENUBAR = "MenuBar"
    TAB = "Tab"
    TABITEM = "TabItem"
    TREE = "Tree"
    TREEITEM = "TreeItem"
    TOOLBAR = "ToolBar"
    STATUSBAR = "StatusBar"
    PROGRESSBAR = "ProgressBar"
    SLIDER = "Slider"
    SPINNER = "Spinner"
    SCROLLBAR = "ScrollBar"
    HYPERLINK = "Hyperlink"
    IMAGE = "Image"
    DOCUMENT = "Document"
    PANE = "Pane"
    WINDOW = "Window"
    TITLEBAR = "TitleBar"
    GROUP = "Group"
    HEADER = "Header"
    HEADERITEM = "HeaderItem"
    TABLE = "Table"
    DATAITEM = "DataItem"
    DATAGRID = "DataGrid"
    CUSTOM = "Custom"
    UNKNOWN = "Unknown"


class MouseButton(str, Enum):
    """鼠标按钮"""
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ScrollDirection(str, Enum):
    """滚动方向"""
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class FindMethod(str, Enum):
    """元素查找方法"""
    AUTO = "auto"       # 自动选择：先 UIA，失败则 Vision
    UIA = "uia"         # 只使用 UIAutomation
    VISION = "vision"   # 只使用视觉识别


class WindowAction(str, Enum):
    """窗口操作类型"""
    LIST = "list"
    SWITCH = "switch"
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    RESTORE = "restore"
    CLOSE = "close"


@dataclass
class BoundingBox:
    """边界框"""
    left: int
    top: int
    right: int
    bottom: int
    
    @property
    def width(self) -> int:
        return self.right - self.left
    
    @property
    def height(self) -> int:
        return self.bottom - self.top
    
    @property
    def center(self) -> tuple[int, int]:
        return (
            self.left + self.width // 2,
            self.top + self.height // 2
        )
    
    def to_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)
    
    def to_region(self) -> tuple[int, int, int, int]:
        """转换为 (x, y, width, height) 格式"""
        return (self.left, self.top, self.width, self.height)
    
    @classmethod
    def from_tuple(cls, t: tuple[int, int, int, int]) -> "BoundingBox":
        return cls(left=t[0], top=t[1], right=t[2], bottom=t[3])
    
    @classmethod
    def from_region(cls, x: int, y: int, width: int, height: int) -> "BoundingBox":
        """从 (x, y, width, height) 创建"""
        return cls(left=x, top=y, right=x + width, bottom=y + height)


@dataclass
class UIElement:
    """
    统一的 UI 元素数据结构
    
    可以来自 UIAutomation 或视觉识别
    """
    # 基本信息
    name: str = ""
    control_type: str = "Unknown"
    bbox: Optional[BoundingBox] = None
    
    # UIAutomation 特有属性
    automation_id: str = ""
    class_name: str = ""
    value: Optional[str] = None
    is_enabled: bool = True
    is_visible: bool = True
    is_focused: bool = False
    
    # 视觉识别特有属性
    description: str = ""
    confidence: float = 1.0
    
    # 来源标识
    source: str = "unknown"  # "uia" 或 "vision"
    
    # 原始控件引用（仅 UIA）
    _control: Any = field(default=None, repr=False)
    
    @property
    def center(self) -> Optional[tuple[int, int]]:
        """获取元素中心点坐标"""
        if self.bbox:
            return self.bbox.center
        return None
    
    def to_dict(self) -> dict:
        """转换为字典（不包含 _control）"""
        return {
            "name": self.name,
            "control_type": self.control_type,
            "bbox": self.bbox.to_tuple() if self.bbox else None,
            "center": self.center,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "value": self.value,
            "is_enabled": self.is_enabled,
            "is_visible": self.is_visible,
            "description": self.description,
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass
class WindowInfo:
    """窗口信息"""
    title: str
    handle: int
    class_name: str = ""
    process_id: int = 0
    process_name: str = ""
    bbox: Optional[BoundingBox] = None
    is_visible: bool = True
    is_minimized: bool = False
    is_maximized: bool = False
    is_focused: bool = False
    
    # 原始窗口引用
    _window: Any = field(default=None, repr=False)
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "title": self.title,
            "handle": self.handle,
            "class_name": self.class_name,
            "process_id": self.process_id,
            "process_name": self.process_name,
            "bbox": self.bbox.to_tuple() if self.bbox else None,
            "is_visible": self.is_visible,
            "is_minimized": self.is_minimized,
            "is_maximized": self.is_maximized,
            "is_focused": self.is_focused,
        }


@dataclass
class ElementLocation:
    """视觉识别返回的元素位置"""
    description: str
    bbox: BoundingBox
    confidence: float = 1.0
    reasoning: str = ""
    
    @property
    def center(self) -> tuple[int, int]:
        return self.bbox.center
    
    def to_ui_element(self) -> UIElement:
        """转换为 UIElement"""
        return UIElement(
            name=self.description,
            control_type="Unknown",
            bbox=self.bbox,
            description=self.description,
            confidence=self.confidence,
            source="vision",
        )


@dataclass
class VisionResult:
    """视觉分析结果"""
    success: bool
    query: str
    answer: str = ""
    elements: list[ElementLocation] = field(default_factory=list)
    raw_response: str = ""
    error: Optional[str] = None


@dataclass
class ScreenshotInfo:
    """截图信息"""
    width: int
    height: int
    monitor: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    region: Optional[tuple[int, int, int, int]] = None  # (x, y, w, h)
    window_title: Optional[str] = None


@dataclass
class ActionResult:
    """操作结果"""
    success: bool
    action: str
    target: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    duration_ms: float = 0
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "action": self.action,
            "target": self.target,
            "message": self.message,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class DesktopState:
    """桌面状态快照"""
    active_window: Optional[WindowInfo] = None
    windows: list[WindowInfo] = field(default_factory=list)
    mouse_position: tuple[int, int] = (0, 0)
    screen_size: tuple[int, int] = (0, 0)
    timestamp: datetime = field(default_factory=datetime.now)
