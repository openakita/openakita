"""
Windows 桌面自动化 - 主控制器

统一接口，智能选择 UIA 或 Vision 方案
"""

import sys
import time
import asyncio
import logging
from typing import Optional, Union, List, Tuple, Any
from PIL import Image

from .config import get_config, DesktopConfig
from .types import (
    UIElement,
    WindowInfo,
    BoundingBox,
    ActionResult,
    FindMethod,
    MouseButton,
    ScrollDirection,
    WindowAction,
)
from .capture import ScreenCapture, get_capture
from .cache import ElementCache, get_cache
from .actions import MouseController, KeyboardController, get_mouse, get_keyboard
from .uia import UIAClient, UIAElementWrapper, UIAInspector, get_uia_client
from .vision import VisionAnalyzer, get_vision_analyzer

# 平台检查
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. "
        f"Current platform: {sys.platform}"
    )

logger = logging.getLogger(__name__)


class DesktopController:
    """
    Windows 桌面控制器
    
    统一接口，智能选择 UIA 或 Vision 方案：
    - 标准 Windows 应用 → UIAutomation（快速准确）
    - 非标准 UI → Vision（通用兜底）
    """
    
    def __init__(
        self,
        config: Optional[DesktopConfig] = None,
    ):
        """
        Args:
            config: 配置对象，None 使用全局配置
        """
        self._config = config or get_config()
        
        # 延迟初始化组件
        self._capture: Optional[ScreenCapture] = None
        self._mouse: Optional[MouseController] = None
        self._keyboard: Optional[KeyboardController] = None
        self._uia: Optional[UIAClient] = None
        self._vision: Optional[VisionAnalyzer] = None
        self._cache: Optional[ElementCache] = None
        self._inspector: Optional[UIAInspector] = None
    
    # ==================== 组件访问器 ====================
    
    @property
    def capture(self) -> ScreenCapture:
        """截图模块"""
        if self._capture is None:
            self._capture = get_capture()
        return self._capture
    
    @property
    def mouse(self) -> MouseController:
        """鼠标控制器"""
        if self._mouse is None:
            self._mouse = get_mouse()
        return self._mouse
    
    @property
    def keyboard(self) -> KeyboardController:
        """键盘控制器"""
        if self._keyboard is None:
            self._keyboard = get_keyboard()
        return self._keyboard
    
    @property
    def uia(self) -> UIAClient:
        """UIAutomation 客户端"""
        if self._uia is None:
            self._uia = get_uia_client()
        return self._uia
    
    @property
    def vision(self) -> VisionAnalyzer:
        """视觉分析器"""
        if self._vision is None:
            self._vision = get_vision_analyzer()
        return self._vision
    
    @property
    def cache(self) -> ElementCache:
        """元素缓存"""
        if self._cache is None:
            self._cache = get_cache()
        return self._cache
    
    @property
    def inspector(self) -> UIAInspector:
        """UIA 检查器"""
        if self._inspector is None:
            self._inspector = UIAInspector(self.uia)
        return self._inspector
    
    # ==================== 截图 ====================
    
    def screenshot(
        self,
        window_title: Optional[str] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
        monitor: Optional[int] = None,
    ) -> Image.Image:
        """
        截取屏幕
        
        Args:
            window_title: 窗口标题，截取指定窗口
            region: 区域 (x, y, width, height)
            monitor: 显示器索引
            
        Returns:
            PIL Image 对象
        """
        if window_title:
            # 查找窗口并截取
            window = self.uia.find_window_fuzzy(window_title, timeout=2.0)
            if window and window.bbox:
                return self.capture.capture_window(window.bbox, window_title)
            logger.warning(f"Window not found: {window_title}, capturing full screen")
        
        return self.capture.capture(monitor=monitor, region=region)
    
    def screenshot_base64(
        self,
        window_title: Optional[str] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
        resize: bool = True,
    ) -> str:
        """截取屏幕并返回 base64"""
        img = self.screenshot(window_title=window_title, region=region)
        return self.capture.to_base64(img, resize_for_api=resize)
    
    # ==================== 元素查找 ====================
    
    async def find_element(
        self,
        target: str,
        window_title: Optional[str] = None,
        method: Union[str, FindMethod] = FindMethod.AUTO,
        timeout: Optional[float] = None,
    ) -> Optional[UIElement]:
        """
        查找 UI 元素
        
        Args:
            target: 元素描述（如"保存按钮"、"name:保存"、"id:btn_save"）
            window_title: 限定在某个窗口内查找
            method: 查找方法 (auto, uia, vision)
            timeout: 超时时间
            
        Returns:
            找到的元素，未找到返回 None
        """
        method = FindMethod(method) if isinstance(method, str) else method
        config = self._config.uia
        search_timeout = timeout or config.timeout
        
        # 解析目标
        parsed = self._parse_target(target)
        
        # 获取搜索根
        root = None
        if window_title:
            root = self.uia.find_window_fuzzy(window_title, timeout=2.0)
            if not root:
                logger.warning(f"Window not found: {window_title}")
        
        # 根据方法选择策略
        if method == FindMethod.UIA:
            return await self._find_by_uia(parsed, root, search_timeout)
        elif method == FindMethod.VISION:
            return await self._find_by_vision(target, window_title)
        else:  # AUTO
            # 先尝试 UIA
            element = await self._find_by_uia(parsed, root, search_timeout / 2)
            if element:
                return element
            
            # 回退到 Vision
            if self._config.vision.enabled:
                logger.info(f"UIA not found, falling back to vision: {target}")
                return await self._find_by_vision(target, window_title)
            
            return None
    
    def _parse_target(self, target: str) -> dict:
        """
        解析目标字符串
        
        支持格式：
        - "name:保存" → 按名称查找
        - "id:btn_save" → 按自动化 ID 查找
        - "type:Button" → 按控件类型查找
        - "保存按钮" → 自然语言描述
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
        root: Optional[UIAElementWrapper],
        timeout: float,
    ) -> Optional[UIElement]:
        """使用 UIAutomation 查找"""
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
        window_title: Optional[str] = None,
    ) -> Optional[UIElement]:
        """使用视觉识别查找"""
        try:
            # 截图
            img = self.screenshot(window_title=window_title)
            
            # 视觉查找
            location = await self.vision.find_element(description, img)
            
            if location:
                return location.to_ui_element()
        except Exception as e:
            logger.error(f"Vision search failed: {e}")
        
        return None
    
    # ==================== 点击操作 ====================
    
    async def click(
        self,
        target: Union[str, Tuple[int, int], UIElement],
        button: Union[str, MouseButton] = MouseButton.LEFT,
        double: bool = False,
        method: Union[str, FindMethod] = FindMethod.AUTO,
    ) -> ActionResult:
        """
        点击目标
        
        Args:
            target: 目标（元素描述、坐标元组或 UIElement）
            button: 鼠标按钮
            double: 是否双击
            method: 元素查找方法
            
        Returns:
            ActionResult
        """
        start_time = time.time()
        
        try:
            # 解析目标坐标
            x, y = await self._resolve_click_target(target, method)
            
            if x is None or y is None:
                return ActionResult(
                    success=False,
                    action="click",
                    target=str(target),
                    error=f"Cannot find target: {target}",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            
            # 执行点击
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
        target: Union[str, Tuple[int, int], UIElement],
        method: FindMethod,
    ) -> Tuple[Optional[int], Optional[int]]:
        """解析点击目标为坐标"""
        if isinstance(target, tuple) and len(target) == 2:
            return target
        
        if isinstance(target, UIElement):
            if target.center:
                return target.center
            return None, None
        
        if isinstance(target, str):
            # 尝试解析坐标字符串 "x,y"
            try:
                parts = target.split(",")
                if len(parts) == 2:
                    return int(parts[0].strip()), int(parts[1].strip())
            except (ValueError, IndexError):
                pass
            
            # 作为元素描述查找
            element = await self.find_element(target, method=method)
            if element and element.center:
                return element.center
        
        return None, None
    
    async def double_click(
        self,
        target: Union[str, Tuple[int, int], UIElement],
        method: Union[str, FindMethod] = FindMethod.AUTO,
    ) -> ActionResult:
        """双击"""
        return await self.click(target, double=True, method=method)
    
    async def right_click(
        self,
        target: Union[str, Tuple[int, int], UIElement],
        method: Union[str, FindMethod] = FindMethod.AUTO,
    ) -> ActionResult:
        """右键点击"""
        return await self.click(target, button=MouseButton.RIGHT, method=method)
    
    # ==================== 输入操作 ====================
    
    def type_text(
        self,
        text: str,
        clear_first: bool = False,
    ) -> ActionResult:
        """
        输入文本
        
        Args:
            text: 要输入的文本
            clear_first: 是否先清空（Ctrl+A）
            
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
                target=text[:50] + "..." if len(text) > 50 else text,
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )
    
    def hotkey(self, *keys: str) -> ActionResult:
        """执行快捷键"""
        return self.keyboard.hotkey(*keys)
    
    def press(self, key: str) -> ActionResult:
        """按下按键"""
        return self.keyboard.press(key)
    
    # ==================== 滚动操作 ====================
    
    def scroll(
        self,
        direction: Union[str, ScrollDirection],
        amount: int = 3,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> ActionResult:
        """
        滚动
        
        Args:
            direction: 方向 (up, down, left, right)
            amount: 滚动量
            x, y: 滚动位置
            
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
    
    # ==================== 窗口管理 ====================
    
    def list_windows(
        self,
        visible_only: bool = True,
    ) -> List[WindowInfo]:
        """列出所有窗口"""
        return self.uia.list_windows(visible_only=visible_only)
    
    def get_active_window(self) -> Optional[WindowInfo]:
        """获取当前活动窗口"""
        window = self.uia.get_active_window()
        if window:
            return window.to_window_info()
        return None
    
    def switch_to_window(self, title: str) -> ActionResult:
        """
        切换到指定窗口
        
        Args:
            title: 窗口标题（模糊匹配）
            
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
        action: Union[str, WindowAction],
        title: Optional[str] = None,
    ) -> ActionResult:
        """
        窗口操作
        
        Args:
            action: 操作类型
            title: 窗口标题
            
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
        
        # 其他操作需要窗口标题
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
    
    # ==================== 等待功能 ====================
    
    async def wait_for_element(
        self,
        target: str,
        timeout: float = 10,
        interval: float = 0.5,
        method: Union[str, FindMethod] = FindMethod.AUTO,
    ) -> Optional[UIElement]:
        """
        等待元素出现
        
        Args:
            target: 元素描述
            timeout: 超时时间
            interval: 检查间隔
            method: 查找方法
            
        Returns:
            找到的元素，超时返回 None
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
        等待窗口出现
        
        Args:
            title: 窗口标题
            timeout: 超时时间
            interval: 检查间隔
            
        Returns:
            是否找到窗口
        """
        window = self.uia.wait_for_window(
            title_re=f".*{title}.*",
            timeout=timeout,
            interval=interval,
        )
        return window is not None
    
    # ==================== 检查功能 ====================
    
    def inspect(
        self,
        window_title: Optional[str] = None,
        depth: int = 2,
    ) -> dict:
        """
        检查窗口的 UI 元素树
        
        Args:
            window_title: 窗口标题，None 使用活动窗口
            depth: 遍历深度
            
        Returns:
            元素树字典
        """
        root = None
        if window_title:
            root = self.uia.find_window_fuzzy(window_title, timeout=2.0)
        
        return self.inspector.get_element_tree(root, depth=depth)
    
    def inspect_text(
        self,
        window_title: Optional[str] = None,
        depth: int = 2,
    ) -> str:
        """
        检查窗口的 UI 元素树（文本格式）
        
        Args:
            window_title: 窗口标题
            depth: 遍历深度
            
        Returns:
            格式化的树文本
        """
        root = None
        if window_title:
            root = self.uia.find_window_fuzzy(window_title, timeout=2.0)
        
        return self.inspector.print_element_tree(root, depth=depth)
    
    # ==================== 视觉分析 ====================
    
    async def analyze_screen(
        self,
        window_title: Optional[str] = None,
        query: Optional[str] = None,
    ) -> dict:
        """
        分析屏幕内容
        
        Args:
            window_title: 窗口标题
            query: 自定义查询，None 则进行通用分析
            
        Returns:
            分析结果
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


# 全局实例
_controller: Optional[DesktopController] = None


def get_controller() -> DesktopController:
    """获取全局控制器"""
    global _controller
    if _controller is None:
        _controller = DesktopController()
    return _controller
