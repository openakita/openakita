"""
Windows desktop automation — Agent tool definitions

Defines the tools used by the OpenAkita Agent.
"""

import logging
import sys
from typing import Any

# Platform check
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

logger = logging.getLogger(__name__)


# ==================== Tool definitions ====================

DESKTOP_TOOLS = [
    {
        "name": "desktop_screenshot",
        "category": "Desktop",
        "description": "Capture Windows desktop screenshot with automatic file saving. When you need to: (1) Show user the desktop state, (2) Capture application windows, (3) Record operation results. IMPORTANT: Must actually call this tool - never say 'screenshot done' without calling. Returns file_path for deliver_artifacts. For browser-only screenshots, use browser_screenshot instead.",
        "detail": """Capture a Windows desktop screenshot and save it to a file.

**Important warning**:
- When the user asks for a screenshot, you must actually call this tool
- Do not claim "screenshot done" without calling it

**Workflow**:
1. Call this tool to capture a screenshot
2. Get the returned file_path
3. Use deliver_artifacts(artifacts=[{type:"image", path:file_path, caption:"..."}]) to deliver it to the user

**Use cases**:
- Desktop application operations
- Viewing the overall desktop state
- Mixed desktop-and-browser operations

**Optional features**:
- window_title: capture only the specified window
- analyze: analyze the screenshot with a vision model

**Note**: If the task only involves in-browser web operations, use browser_screenshot instead.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Save path (optional). When omitted, auto-generates desktop_screenshot_YYYYMMDD_HHMMSS.png.",
                },
                "window_title": {
                    "type": "string",
                    "description": "Optional; capture only the specified window (fuzzy title match).",
                },
                "analyze": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to analyze the screenshot with a vision model.",
                },
                "analyze_query": {
                    "type": "string",
                    "description": "Analysis query, e.g. 'find all buttons' (requires analyze=true).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "desktop_find_element",
        "category": "Desktop",
        "description": "Find desktop UI elements using UIAutomation (fast, accurate) or vision recognition (fallback). When you need to: (1) Locate buttons/menus/icons, (2) Get element positions before clicking, (3) Verify UI state. Supports: natural language ('save button'), name: prefix, id: prefix, type: prefix. For browser webpage elements, use browser_* tools instead.",
        "detail": """Find a desktop UI element. Prefers UIAutomation (fast and accurate); falls back to vision recognition (generic) on failure.

**Supported query formats**:
- Natural language: "save button", "red icon"
- By name: "name:Save"
- By ID: "id:btn_save"
- By type: "type:Button"

**Lookup methods**:
- auto: automatic selection (recommended)
- uia: UIAutomation only
- vision: vision recognition only

**Returned information**:
- Element position (x, y)
- Element size
- Element attributes

**Note**: If the target is an in-browser web element, use the browser_* tools instead.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Element description, e.g. 'save button', 'name:File', 'id:btn_ok'.",
                },
                "window_title": {"type": "string", "description": "Optional; restrict the search to a specific window."},
                "method": {
                    "type": "string",
                    "enum": ["auto", "uia", "vision"],
                    "default": "auto",
                    "description": "Lookup method: auto picks automatically, uia uses UIAutomation only, vision uses vision only.",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "desktop_click",
        "category": "Desktop",
        "description": "Click desktop elements or coordinates. When you need to: (1) Click buttons/icons in applications, (2) Select menu items, (3) Interact with desktop UI. Supports: element description ('save button'), name: prefix, coordinates ('100,200'). Left/right/middle button and double-click supported. For browser webpage elements, use browser tools (browser_navigate, browser_get_content, etc.).",
        "detail": """Click a UI element on the desktop or click specific coordinates.

**Supported target formats**:
- Element description: "save button", "name:OK"
- Coordinates: "100,200"

**Click options**:
- button: left/right/middle
- double: whether to double-click

**Element lookup method**:
- auto: automatic selection (recommended)
- uia: UIAutomation only
- vision: vision recognition only

**Note**: If you are clicking an in-browser web element, use the browser tools (browser_navigate, browser_get_content, etc.).""",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Element description (e.g. 'OK button') or coordinates (e.g. '100,200').",
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                    "description": "Mouse button.",
                },
                "double": {"type": "boolean", "default": False, "description": "Whether to double-click."},
                "method": {
                    "type": "string",
                    "enum": ["auto", "uia", "vision"],
                    "default": "auto",
                    "description": "Element lookup method.",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "desktop_type",
        "category": "Desktop",
        "description": "Type text at current cursor position in desktop applications. When you need to: (1) Enter text in application dialogs, (2) Fill input fields, (3) Type in text editors. Supports Chinese input. Use clear_first=true to replace existing text. For browser webpage forms, use browser tools.",
        "detail": """Type text at the current focus.

**Features**:
- Supports Chinese input
- Optionally clear the field before typing

**Parameters**:
- text: the text to type
- clear_first: whether to clear first (Ctrl+A then type)

**Usage tip**:
- Click the target input field to give it focus first
- Then call this tool to type

**Note**: If you are typing into an in-browser web form, use the browser tools (browser_navigate, browser_get_content, etc.).""",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type."},
                "clear_first": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to clear existing content first (Ctrl+A then type).",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "desktop_hotkey",
        "category": "Desktop",
        "description": "Execute keyboard shortcuts. When you need to: (1) Copy/paste (Ctrl+C/V), (2) Save files (Ctrl+S), (3) Close windows (Alt+F4), (4) Undo/redo (Ctrl+Z/Y), (5) Select all (Ctrl+A). Common shortcuts: ['ctrl','c'], ['ctrl','v'], ['ctrl','s'], ['alt','f4'], ['ctrl','z'].",
        "detail": """Execute a keyboard shortcut.

**Common shortcuts**:
- ['ctrl', 'c']: copy
- ['ctrl', 'v']: paste
- ['ctrl', 'x']: cut
- ['ctrl', 's']: save
- ['ctrl', 'z']: undo
- ['ctrl', 'y']: redo
- ['ctrl', 'a']: select all
- ['alt', 'f4']: close window
- ['alt', 'tab']: switch window
- ['win', 'd']: show desktop

**Parameter format**:
keys is an array of key names, e.g. ['ctrl', 'c'].""",
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key combination, e.g. ['ctrl', 'c'] or ['alt', 'f4'].",
                }
            },
            "required": ["keys"],
        },
    },
    {
        "name": "desktop_scroll",
        "category": "Desktop",
        "description": "Scroll mouse wheel in specified direction. When you need to: (1) Scroll page/document content, (2) Navigate long lists, (3) Zoom in/out (with Ctrl). Directions: up/down/left/right. Default amount is 3 scroll units.",
        "detail": """Scroll the mouse wheel.

**Supported directions**:
- up: scroll up
- down: scroll down
- left: scroll left
- right: scroll right

**Parameters**:
- direction: scroll direction
- amount: number of scroll units (default 3)

**Use cases**:
- Scroll page/document content
- Browse long lists
- Zoom when combined with the Ctrl key""",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "description": "Scroll direction.",
                },
                "amount": {"type": "integer", "default": 3, "description": "Number of scroll units."},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "desktop_window",
        "category": "Desktop",
        "description": "Window management operations. When you need to: (1) List all open windows, (2) Switch to a specific window, (3) Minimize/maximize/restore windows, (4) Close windows. Actions: list, switch, minimize, maximize, restore, close. Use title parameter for targeting specific window (fuzzy match).",
        "detail": """Window management operations.

**Supported actions**:
- list: list all windows
- switch: switch to the specified window (activate and bring to front)
- minimize: minimize the window
- maximize: maximize the window
- restore: restore the window
- close: close the window

**Parameters**:
- action: action type (required)
- title: window title (fuzzy match); not required for the list action

**Returned information** (list action):
- Window title
- Window handle
- Window position and size""",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "switch", "minimize", "maximize", "restore", "close"],
                    "description": "Action type.",
                },
                "title": {"type": "string", "description": "Window title (fuzzy match); not required for the list action."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "desktop_wait",
        "category": "Desktop",
        "description": "Wait for UI element or window to appear. When you need to: (1) Wait for dialog to open, (2) Wait for loading to complete, (3) Synchronize with application state before next action. Target types: element (UI element), window (window title). Default timeout is 10 seconds.",
        "detail": """Wait for a UI element or window to appear.

**Use cases**:
- Wait for a dialog to open
- Wait for loading to finish
- Synchronize with application state before the next action

**Target types**:
- element: wait for a UI element
- window: wait for a window

**Parameters**:
- target: element description or window title
- target_type: target type (default: element)
- timeout: timeout (default: 10 seconds)

**Result**:
- Success: element/window info
- Timeout: error message""",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Element description or window title."},
                "target_type": {
                    "type": "string",
                    "enum": ["element", "window"],
                    "default": "element",
                    "description": "Target type.",
                },
                "timeout": {"type": "integer", "default": 10, "description": "Timeout in seconds."},
            },
            "required": ["target"],
        },
    },
    {
        "name": "desktop_inspect",
        "category": "Desktop",
        "description": "Inspect window UI element tree structure for debugging and understanding interface layout. When you need to: (1) Debug UI automation issues, (2) Understand application structure, (3) Find correct element identifiers for clicking/typing. Returns element names, types, and IDs at specified depth.",
        "detail": """Inspect a window's UI element tree (for debugging and understanding the interface).

**Use cases**:
- Debug UI automation issues
- Understand an application's UI structure
- Find the correct element identifiers

**Parameters**:
- window_title: window title (when omitted, inspects the currently active window)
- depth: traversal depth of the element tree (default: 2)

**Returned information**:
- Element name
- Element type
- Element ID
- Element position
- Child element list""",
        "input_schema": {
            "type": "object",
            "properties": {
                "window_title": {
                    "type": "string",
                    "description": "Window title; when omitted, inspects the currently active window.",
                },
                "depth": {"type": "integer", "default": 2, "description": "Traversal depth of the element tree."},
            },
            "required": [],
        },
    },
    {
        "name": "desktop_batch",
        "category": "Desktop",
        "description": (
            "Execute multiple desktop automation actions atomically in sequence. "
            "Use when you need to perform several quick operations (click, type, hotkey) "
            "without screenshots between each step. Each action is a dict with 'tool' "
            "and 'params' keys. Reduces round-trips for multi-step UI interactions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "enum": [
                                    "desktop_click",
                                    "desktop_type",
                                    "desktop_hotkey",
                                    "desktop_scroll",
                                    "desktop_wait",
                                ],
                                "description": "The desktop tool to execute.",
                            },
                            "params": {
                                "type": "object",
                                "description": "Parameters for the tool.",
                            },
                        },
                        "required": ["tool", "params"],
                    },
                    "description": "Array of actions to execute in sequence.",
                },
            },
            "required": ["actions"],
        },
    },
]


# ==================== Tool handler ====================


class DesktopToolHandler:
    """
    Desktop tool handler.

    Handles tool-call requests from the Agent.
    """

    def __init__(self):
        self._controller = None

    @property
    def controller(self):
        """Lazily load the controller."""
        if self._controller is None:
            from .controller import get_controller

            self._controller = get_controller()
        return self._controller

    async def handle(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Handle a tool call.

        Args:
            tool_name: tool name
            params: parameter dict

        Returns:
            result dict
        """
        try:
            if tool_name == "desktop_screenshot":
                return await self._handle_screenshot(params)
            elif tool_name == "desktop_find_element":
                return await self._handle_find_element(params)
            elif tool_name == "desktop_click":
                return await self._handle_click(params)
            elif tool_name == "desktop_type":
                return self._handle_type(params)
            elif tool_name == "desktop_hotkey":
                return self._handle_hotkey(params)
            elif tool_name == "desktop_scroll":
                return self._handle_scroll(params)
            elif tool_name == "desktop_window":
                return self._handle_window(params)
            elif tool_name == "desktop_wait":
                return await self._handle_wait(params)
            elif tool_name == "desktop_inspect":
                return self._handle_inspect(params)
            elif tool_name == "desktop_batch":
                return await self._handle_batch(params)
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return {"error": str(e)}

    async def _handle_screenshot(self, params: dict) -> dict:
        """Handle a screenshot request."""
        import os
        from datetime import datetime

        path = params.get("path")
        window_title = params.get("window_title")
        analyze = params.get("analyze", False)
        analyze_query = params.get("analyze_query")

        # Capture
        img = self.controller.screenshot(window_title=window_title)

        # Generate save path
        if not path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"desktop_screenshot_{timestamp}.png"
            # Save to the user's desktop
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            if os.path.exists(desktop_path):
                path = os.path.join(desktop_path, filename)
            else:
                # If the desktop directory doesn't exist, save to the current directory
                path = filename

        # Save the screenshot
        self.controller.capture.save(img, path)
        abs_path = os.path.abspath(path)

        result = {
            "success": True,
            "file_path": abs_path,
            "width": img.width,
            "height": img.height,
        }

        # Optional analysis
        if analyze:
            analysis = await self.controller.analyze_screen(
                window_title=window_title,
                query=analyze_query,
            )
            result["analysis"] = analysis

        return result

    async def _handle_find_element(self, params: dict) -> dict:
        """Handle a find-element request."""
        target = params.get("target")
        window_title = params.get("window_title")
        method = params.get("method", "auto")

        element = await self.controller.find_element(
            target=target,
            window_title=window_title,
            method=method,
        )

        if element:
            return {
                "success": True,
                "found": True,
                "element": element.to_dict(),
            }
        else:
            return {
                "success": True,
                "found": False,
                "message": f"Element not found: {target}",
            }

    async def _handle_click(self, params: dict) -> dict:
        """Handle a click request."""
        target = params.get("target")
        button = params.get("button", "left")
        double = params.get("double", False)
        method = params.get("method", "auto")

        result = await self.controller.click(
            target=target,
            button=button,
            double=double,
            method=method,
        )

        return result.to_dict()

    def _handle_type(self, params: dict) -> dict:
        """Handle a type request."""
        text = params.get("text", "")
        clear_first = params.get("clear_first", False)

        result = self.controller.type_text(text, clear_first=clear_first)
        return result.to_dict()

    def _handle_hotkey(self, params: dict) -> dict:
        """Handle a hotkey request."""
        keys = params.get("keys", [])

        if not keys:
            return {"error": "No keys provided"}

        result = self.controller.hotkey(*keys)
        return result.to_dict()

    def _handle_scroll(self, params: dict) -> dict:
        """Handle a scroll request."""
        direction = params.get("direction", "down")
        amount = params.get("amount", 3)

        result = self.controller.scroll(direction, amount)
        return result.to_dict()

    def _handle_window(self, params: dict) -> dict:
        """Handle a window-action request."""
        action = params.get("action")
        title = params.get("title")

        if action == "list":
            windows = self.controller.list_windows()
            return {
                "success": True,
                "windows": [w.to_dict() for w in windows],
                "count": len(windows),
            }

        result = self.controller.window_action(action, title)
        return result.to_dict()

    async def _handle_wait(self, params: dict) -> dict:
        """Handle a wait request."""
        target = params.get("target")
        target_type = params.get("target_type", "element")
        timeout = params.get("timeout", 10)

        if target_type == "window":
            found = await self.controller.wait_for_window(target, timeout=timeout)
            return {
                "success": True,
                "found": found,
                "target": target,
                "target_type": "window",
            }
        else:
            element = await self.controller.wait_for_element(target, timeout=timeout)
            if element:
                return {
                    "success": True,
                    "found": True,
                    "element": element.to_dict(),
                }
            else:
                return {
                    "success": True,
                    "found": False,
                    "message": f"Element not found within {timeout}s: {target}",
                }

    def _handle_inspect(self, params: dict) -> dict:
        """Handle an inspect request."""
        window_title = params.get("window_title")
        depth = params.get("depth", 2)

        tree = self.controller.inspect(window_title=window_title, depth=depth)
        text = self.controller.inspect_text(window_title=window_title, depth=depth)

        return {
            "success": True,
            "tree": tree,
            "text": text,
        }

    async def _handle_batch(self, params: dict) -> dict:
        """Execute multiple desktop actions atomically in sequence.

        Modeled after CC computer_batch: atomic batch execution.
        """
        actions = params.get("actions", [])
        if not actions:
            return {"error": "desktop_batch requires a non-empty 'actions' array."}
        if len(actions) > 20:
            return {"error": "desktop_batch supports at most 20 actions per call."}

        allowed = {
            "desktop_click",
            "desktop_type",
            "desktop_hotkey",
            "desktop_scroll",
            "desktop_wait",
        }
        results = []
        for i, action in enumerate(actions):
            tool = action.get("tool", "")
            action_params = action.get("params", {})
            if tool not in allowed:
                results.append({"step": i, "error": f"Tool '{tool}' not allowed in batch."})
                continue
            try:
                result = await self.handle(tool, action_params)
                results.append({"step": i, "result": result})
            except Exception as e:
                results.append({"step": i, "error": str(e)})
                break  # abort on first failure for atomicity

        return {
            "success": all("error" not in r for r in results),
            "steps_completed": len(results),
            "results": results,
        }


# Global tool handler
_handler: DesktopToolHandler | None = None


def get_tool_handler() -> DesktopToolHandler:
    """Get the global tool handler."""
    global _handler
    if _handler is None:
        _handler = DesktopToolHandler()
    return _handler


def register_desktop_tools(agent: Any) -> None:
    """
    Register the desktop tools with the Agent.

    Args:
        agent: OpenAkita Agent instance
    """
    handler = get_tool_handler()

    # Register tool definitions
    if hasattr(agent, "register_tools"):
        agent.register_tools(DESKTOP_TOOLS)

    # Register the handler
    if hasattr(agent, "register_tool_handler"):
        for tool in DESKTOP_TOOLS:
            agent.register_tool_handler(tool["name"], handler.handle)

    logger.info(f"Registered {len(DESKTOP_TOOLS)} desktop tools")
