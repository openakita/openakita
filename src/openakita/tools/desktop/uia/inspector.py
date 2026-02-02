"""
Windows 桌面自动化 - UIAutomation 检查器

提供元素树查看和调试功能
"""

import sys
from typing import Optional, List, Dict, Any
import logging

from .client import UIAClient, get_uia_client
from .elements import UIAElementWrapper

# 平台检查
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. "
        f"Current platform: {sys.platform}"
    )

logger = logging.getLogger(__name__)


class UIAInspector:
    """
    UIAutomation 检查器
    
    用于查看和调试 UI 元素结构
    """
    
    def __init__(self, client: Optional[UIAClient] = None):
        """
        Args:
            client: UIA 客户端，None 使用全局实例
        """
        self._client = client or get_uia_client()
    
    def get_element_tree(
        self,
        root: Optional[UIAElementWrapper] = None,
        depth: int = 3,
        include_invisible: bool = False,
    ) -> Dict[str, Any]:
        """
        获取元素树结构
        
        Args:
            root: 根元素，None 使用活动窗口
            depth: 遍历深度
            include_invisible: 是否包含不可见元素
            
        Returns:
            树结构字典
        """
        if root is None:
            root = self._client.get_active_window()
            if root is None:
                return {"error": "No active window found"}
        
        return self._build_tree(root, depth, include_invisible)
    
    def _build_tree(
        self,
        element: UIAElementWrapper,
        depth: int,
        include_invisible: bool,
        current_depth: int = 0,
    ) -> Dict[str, Any]:
        """
        递归构建元素树
        
        Args:
            element: 当前元素
            depth: 最大深度
            include_invisible: 是否包含不可见元素
            current_depth: 当前深度
            
        Returns:
            元素及其子元素的树结构
        """
        # 基本信息
        node = {
            "name": element.name,
            "control_type": element.control_type,
            "automation_id": element.automation_id,
            "class_name": element.class_name,
            "bbox": element.bbox.to_tuple() if element.bbox else None,
            "is_enabled": element.is_enabled,
            "is_visible": element.is_visible,
            "value": element.value,
        }
        
        # 如果还有深度，获取子元素
        if current_depth < depth:
            children = []
            try:
                for child in element.get_children():
                    # 过滤不可见元素
                    if not include_invisible and not child.is_visible:
                        continue
                    
                    child_node = self._build_tree(
                        child,
                        depth,
                        include_invisible,
                        current_depth + 1,
                    )
                    children.append(child_node)
            except Exception as e:
                logger.debug(f"Failed to get children: {e}")
            
            if children:
                node["children"] = children
        
        return node
    
    def print_element_tree(
        self,
        root: Optional[UIAElementWrapper] = None,
        depth: int = 3,
        include_invisible: bool = False,
        indent: str = "  ",
    ) -> str:
        """
        打印元素树（文本格式）
        
        Args:
            root: 根元素
            depth: 遍历深度
            include_invisible: 是否包含不可见元素
            indent: 缩进字符串
            
        Returns:
            格式化的树文本
        """
        if root is None:
            root = self._client.get_active_window()
            if root is None:
                return "No active window found"
        
        lines = []
        self._print_tree_recursive(
            root, depth, include_invisible, indent, lines, 0
        )
        return "\n".join(lines)
    
    def _print_tree_recursive(
        self,
        element: UIAElementWrapper,
        depth: int,
        include_invisible: bool,
        indent: str,
        lines: List[str],
        current_depth: int,
    ) -> None:
        """递归打印元素树"""
        # 构建当前行
        prefix = indent * current_depth
        
        # 元素信息
        name = element.name or "(no name)"
        ctrl_type = element.control_type
        auto_id = element.automation_id
        
        # 格式化
        info_parts = [f"[{ctrl_type}]", f'"{name}"']
        if auto_id:
            info_parts.append(f"(id={auto_id})")
        if element.bbox:
            center = element.bbox.center
            info_parts.append(f"@{center}")
        
        lines.append(f"{prefix}{' '.join(info_parts)}")
        
        # 递归子元素
        if current_depth < depth:
            try:
                for child in element.get_children():
                    if not include_invisible and not child.is_visible:
                        continue
                    
                    self._print_tree_recursive(
                        child,
                        depth,
                        include_invisible,
                        indent,
                        lines,
                        current_depth + 1,
                    )
            except Exception as e:
                logger.debug(f"Failed to get children: {e}")
    
    def find_elements_by_text(
        self,
        text: str,
        root: Optional[UIAElementWrapper] = None,
        exact_match: bool = False,
    ) -> List[UIAElementWrapper]:
        """
        按文本内容查找元素
        
        Args:
            text: 要查找的文本
            root: 搜索根元素
            exact_match: 是否精确匹配
            
        Returns:
            匹配的元素列表
        """
        if root is None:
            root = self._client.get_active_window()
            if root is None:
                return []
        
        results = []
        self._search_by_text(root, text, exact_match, results)
        return results
    
    def _search_by_text(
        self,
        element: UIAElementWrapper,
        text: str,
        exact_match: bool,
        results: List[UIAElementWrapper],
        max_depth: int = 10,
        current_depth: int = 0,
    ) -> None:
        """递归按文本搜索"""
        if current_depth > max_depth:
            return
        
        # 检查当前元素
        name = element.name or ""
        value = element.value or ""
        
        if exact_match:
            if text == name or text == value:
                results.append(element)
        else:
            text_lower = text.lower()
            if text_lower in name.lower() or text_lower in value.lower():
                results.append(element)
        
        # 递归子元素
        try:
            for child in element.get_children():
                self._search_by_text(
                    child, text, exact_match, results,
                    max_depth, current_depth + 1
                )
        except Exception:
            pass
    
    def find_clickable_elements(
        self,
        root: Optional[UIAElementWrapper] = None,
    ) -> List[UIAElementWrapper]:
        """
        查找所有可点击元素
        
        Args:
            root: 搜索根元素
            
        Returns:
            可点击元素列表
        """
        clickable_types = {
            "Button", "MenuItem", "Hyperlink", "TabItem",
            "ListItem", "TreeItem", "CheckBox", "RadioButton",
        }
        
        if root is None:
            root = self._client.get_active_window()
            if root is None:
                return []
        
        results = []
        self._find_by_types(root, clickable_types, results)
        return results
    
    def find_input_elements(
        self,
        root: Optional[UIAElementWrapper] = None,
    ) -> List[UIAElementWrapper]:
        """
        查找所有输入元素
        
        Args:
            root: 搜索根元素
            
        Returns:
            输入元素列表
        """
        input_types = {"Edit", "ComboBox", "Spinner", "Slider"}
        
        if root is None:
            root = self._client.get_active_window()
            if root is None:
                return []
        
        results = []
        self._find_by_types(root, input_types, results)
        return results
    
    def _find_by_types(
        self,
        element: UIAElementWrapper,
        control_types: set,
        results: List[UIAElementWrapper],
        max_depth: int = 10,
        current_depth: int = 0,
    ) -> None:
        """按控件类型查找"""
        if current_depth > max_depth:
            return
        
        # 检查当前元素
        if element.control_type in control_types and element.is_enabled:
            results.append(element)
        
        # 递归子元素
        try:
            for child in element.get_children():
                self._find_by_types(
                    child, control_types, results,
                    max_depth, current_depth + 1
                )
        except Exception:
            pass
    
    def get_element_at_point(
        self,
        x: int,
        y: int,
    ) -> Optional[UIAElementWrapper]:
        """
        获取指定坐标处的元素
        
        Args:
            x, y: 屏幕坐标
            
        Returns:
            该坐标处的元素
        """
        try:
            from pywinauto import Desktop
            from pywinauto.uia_element_info import UIAElementInfo
            import comtypes.client
            
            # 使用 UI Automation API 获取坐标处的元素
            uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=comtypes.gen.UIAutomationClient.IUIAutomation,
            )
            
            element = uia.ElementFromPoint(
                comtypes.gen.UIAutomationClient.tagPOINT(x, y)
            )
            
            if element:
                # 包装为 pywinauto 元素
                from pywinauto.controls.uiawrapper import UIAWrapper
                elem_info = UIAElementInfo(element)
                wrapper = UIAWrapper(elem_info)
                return UIAElementWrapper(wrapper)
                
        except Exception as e:
            logger.debug(f"Failed to get element at point ({x}, {y}): {e}")
        
        return None
    
    def describe_element(
        self,
        element: UIAElementWrapper,
    ) -> str:
        """
        生成元素的描述文本
        
        Args:
            element: 要描述的元素
            
        Returns:
            描述文本
        """
        parts = []
        
        # 控件类型
        ctrl_type = element.control_type
        if ctrl_type:
            parts.append(f"类型: {ctrl_type}")
        
        # 名称
        name = element.name
        if name:
            parts.append(f"名称: {name}")
        
        # 自动化 ID
        auto_id = element.automation_id
        if auto_id:
            parts.append(f"ID: {auto_id}")
        
        # 类名
        class_name = element.class_name
        if class_name:
            parts.append(f"类: {class_name}")
        
        # 位置
        bbox = element.bbox
        if bbox:
            parts.append(f"位置: ({bbox.left}, {bbox.top}) - ({bbox.right}, {bbox.bottom})")
            parts.append(f"中心: {bbox.center}")
        
        # 状态
        states = []
        if element.is_enabled:
            states.append("可用")
        else:
            states.append("禁用")
        if element.is_visible:
            states.append("可见")
        else:
            states.append("隐藏")
        if element.is_focused:
            states.append("有焦点")
        parts.append(f"状态: {', '.join(states)}")
        
        # 值
        value = element.value
        if value:
            parts.append(f"值: {value}")
        
        return "\n".join(parts)
