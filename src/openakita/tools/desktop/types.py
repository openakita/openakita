"""
Windows Desktop Automation - Data Type Definitions

Defines shared data structures used across all modules
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ControlType(StrEnum):
    """Windows UI control types"""

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


class MouseButton(StrEnum):
    """Mouse buttons"""

    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ScrollDirection(StrEnum):
    """Scroll directions"""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class FindMethod(StrEnum):
    """Element find methods"""

    AUTO = "auto"  # Auto-select: UIA first, fall back to Vision
    UIA = "uia"  # UIAutomation only
    VISION = "vision"  # Visual recognition only


class WindowAction(StrEnum):
    """Window action types"""

    LIST = "list"
    SWITCH = "switch"
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    RESTORE = "restore"
    CLOSE = "close"


@dataclass
class BoundingBox:
    """Bounding box"""

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
        return (self.left + self.width // 2, self.top + self.height // 2)

    def to_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)

    def to_region(self) -> tuple[int, int, int, int]:
        """Convert to (x, y, width, height) format"""
        return (self.left, self.top, self.width, self.height)

    @classmethod
    def from_tuple(cls, t: tuple[int, int, int, int]) -> "BoundingBox":
        return cls(left=t[0], top=t[1], right=t[2], bottom=t[3])

    @classmethod
    def from_region(cls, x: int, y: int, width: int, height: int) -> "BoundingBox":
        """Create from (x, y, width, height)"""
        return cls(left=x, top=y, right=x + width, bottom=y + height)


@dataclass
class UIElement:
    """
    Unified UI element data structure

    Can originate from UIAutomation or visual recognition
    """

    # Basic info
    name: str = ""
    control_type: str = "Unknown"
    bbox: BoundingBox | None = None

    # UIAutomation-specific attributes
    automation_id: str = ""
    class_name: str = ""
    value: str | None = None
    is_enabled: bool = True
    is_visible: bool = True
    is_focused: bool = False

    # Visual-recognition-specific attributes
    description: str = ""
    confidence: float = 1.0

    # Source identifier
    source: str = "unknown"  # "uia" or "vision"

    # Raw control reference (UIA only)
    _control: Any = field(default=None, repr=False)

    @property
    def center(self) -> tuple[int, int] | None:
        """Get element center coordinates"""
        if self.bbox:
            return self.bbox.center
        return None

    def to_dict(self) -> dict:
        """Convert to dict (excludes _control)"""
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
    """Window info"""

    title: str
    handle: int
    class_name: str = ""
    process_id: int = 0
    process_name: str = ""
    bbox: BoundingBox | None = None
    is_visible: bool = True
    is_minimized: bool = False
    is_maximized: bool = False
    is_focused: bool = False

    # Raw window reference
    _window: Any = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """Convert to dict"""
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
    """Element location returned by visual recognition"""

    description: str
    bbox: BoundingBox
    confidence: float = 1.0
    reasoning: str = ""

    @property
    def center(self) -> tuple[int, int]:
        return self.bbox.center

    def to_ui_element(self) -> UIElement:
        """Convert to UIElement"""
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
    """Visual analysis result"""

    success: bool
    query: str
    answer: str = ""
    elements: list[ElementLocation] = field(default_factory=list)
    raw_response: str = ""
    error: str | None = None


@dataclass
class ScreenshotInfo:
    """Screenshot info"""

    width: int
    height: int
    monitor: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    region: tuple[int, int, int, int] | None = None  # (x, y, w, h)
    window_title: str | None = None


@dataclass
class ActionResult:
    """Action result"""

    success: bool
    action: str
    target: str | None = None
    message: str = ""
    error: str | None = None
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
    """Desktop state snapshot"""

    active_window: WindowInfo | None = None
    windows: list[WindowInfo] = field(default_factory=list)
    mouse_position: tuple[int, int] = (0, 0)
    screen_size: tuple[int, int] = (0, 0)
    timestamp: datetime = field(default_factory=datetime.now)
