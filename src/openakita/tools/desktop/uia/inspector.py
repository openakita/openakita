"""
Windows desktop automation - UIAutomation inspector.

Provides element tree inspection and debugging capabilities.
"""

import logging
import sys
from typing import Any

from .client import UIAClient, get_uia_client
from .elements import UIAElementWrapper

# Platform check
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

logger = logging.getLogger(__name__)


class UIAInspector:
    """
    UIAutomation inspector.

    Used for inspecting and debugging UI element structures.
    """

    def __init__(self, client: UIAClient | None = None):
        """
        Args:
            client: UIA client; None uses the global instance.
        """
        self._client = client or get_uia_client()

    def get_element_tree(
        self,
        root: UIAElementWrapper | None = None,
        depth: int = 3,
        include_invisible: bool = False,
    ) -> dict[str, Any]:
        """
        Get the element tree structure.

        Args:
            root: Root element; None uses the active window.
            depth: Traversal depth.
            include_invisible: Whether to include invisible elements.

        Returns:
            Tree structure dictionary.
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
    ) -> dict[str, Any]:
        """
        Recursively build the element tree.

        Args:
            element: Current element.
            depth: Maximum depth.
            include_invisible: Whether to include invisible elements.
            current_depth: Current depth.

        Returns:
            Tree structure of the element and its children.
        """
        # Basic information
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

        # If depth remains, get children
        if current_depth < depth:
            children = []
            try:
                for child in element.get_children():
                    # Filter out invisible elements
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
        root: UIAElementWrapper | None = None,
        depth: int = 3,
        include_invisible: bool = False,
        indent: str = "  ",
    ) -> str:
        """
        Print the element tree in text format.

        Args:
            root: Root element.
            depth: Traversal depth.
            include_invisible: Whether to include invisible elements.
            indent: Indentation string.

        Returns:
            Formatted tree text.
        """
        if root is None:
            root = self._client.get_active_window()
            if root is None:
                return "No active window found"

        lines = []
        self._print_tree_recursive(root, depth, include_invisible, indent, lines, 0)
        return "\n".join(lines)

    def _print_tree_recursive(
        self,
        element: UIAElementWrapper,
        depth: int,
        include_invisible: bool,
        indent: str,
        lines: list[str],
        current_depth: int,
    ) -> None:
        """Recursively print the element tree."""
        # Build the current line
        prefix = indent * current_depth

        # Element information
        name = element.name or "(no name)"
        ctrl_type = element.control_type
        auto_id = element.automation_id

        # Format
        info_parts = [f"[{ctrl_type}]", f'"{name}"']
        if auto_id:
            info_parts.append(f"(id={auto_id})")
        if element.bbox:
            center = element.bbox.center
            info_parts.append(f"@{center}")

        lines.append(f"{prefix}{' '.join(info_parts)}")

        # Recurse into children
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
        root: UIAElementWrapper | None = None,
        exact_match: bool = False,
    ) -> list[UIAElementWrapper]:
        """
        Find elements by text content.

        Args:
            text: Text to search for.
            root: Search root element.
            exact_match: Whether to require an exact match.

        Returns:
            List of matching elements.
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
        results: list[UIAElementWrapper],
        max_depth: int = 10,
        current_depth: int = 0,
    ) -> None:
        """Recursively search by text."""
        if current_depth > max_depth:
            return

        # Check current element
        name = element.name or ""
        value = element.value or ""

        if exact_match:
            if text in (name, value):
                results.append(element)
        else:
            text_lower = text.lower()
            if text_lower in name.lower() or text_lower in value.lower():
                results.append(element)

        # Recurse into children
        try:
            for child in element.get_children():
                self._search_by_text(
                    child, text, exact_match, results, max_depth, current_depth + 1
                )
        except Exception:
            pass

    def find_clickable_elements(
        self,
        root: UIAElementWrapper | None = None,
    ) -> list[UIAElementWrapper]:
        """
        Find all clickable elements.

        Args:
            root: Search root element.

        Returns:
            List of clickable elements.
        """
        clickable_types = {
            "Button",
            "MenuItem",
            "Hyperlink",
            "TabItem",
            "ListItem",
            "TreeItem",
            "CheckBox",
            "RadioButton",
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
        root: UIAElementWrapper | None = None,
    ) -> list[UIAElementWrapper]:
        """
        Find all input elements.

        Args:
            root: Search root element.

        Returns:
            List of input elements.
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
        results: list[UIAElementWrapper],
        max_depth: int = 10,
        current_depth: int = 0,
    ) -> None:
        """Find elements by control type."""
        if current_depth > max_depth:
            return

        # Check current element
        if element.control_type in control_types and element.is_enabled:
            results.append(element)

        # Recurse into children
        try:
            for child in element.get_children():
                self._find_by_types(child, control_types, results, max_depth, current_depth + 1)
        except Exception:
            pass

    def get_element_at_point(
        self,
        x: int,
        y: int,
    ) -> UIAElementWrapper | None:
        """
        Get the element at the specified coordinates.

        Args:
            x, y: Screen coordinates.

        Returns:
            Element at the given coordinates.
        """
        try:
            import comtypes.client
            from pywinauto.uia_element_info import UIAElementInfo

            # Use UI Automation API to get element at coordinates
            uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=comtypes.gen.UIAutomationClient.IUIAutomation,
            )

            element = uia.ElementFromPoint(comtypes.gen.UIAutomationClient.tagPOINT(x, y))

            if element:
                # Wrap as pywinauto element
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
        Generate a description text for the element.

        Args:
            element: Element to describe.

        Returns:
            Description text.
        """
        parts = []

        # Control type
        ctrl_type = element.control_type
        if ctrl_type:
            parts.append(f"Type: {ctrl_type}")

        # Name
        name = element.name
        if name:
            parts.append(f"Name: {name}")

        # Automation ID
        auto_id = element.automation_id
        if auto_id:
            parts.append(f"ID: {auto_id}")

        # Class name
        class_name = element.class_name
        if class_name:
            parts.append(f"Class: {class_name}")

        # Position
        bbox = element.bbox
        if bbox:
            parts.append(f"Position: ({bbox.left}, {bbox.top}) - ({bbox.right}, {bbox.bottom})")
            parts.append(f"Center: {bbox.center}")

        # State
        states = []
        if element.is_enabled:
            states.append("enabled")
        else:
            states.append("disabled")
        if element.is_visible:
            states.append("visible")
        else:
            states.append("hidden")
        if element.is_focused:
            states.append("focused")
        parts.append(f"State: {', '.join(states)}")

        # Value
        value = element.value
        if value:
            parts.append(f"Value: {value}")

        return "\n".join(parts)
