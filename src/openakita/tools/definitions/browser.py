"""
Browser tool definitions

Contains browser automation tools (all based on Playwright, following tool-definition-spec.md):
- browser_open: Launch browser + status query
- browser_navigate: Navigate to URL (recommended to pass URL params directly for search tasks)
- browser_click: Click page element
- browser_type: Type text
- browser_scroll: Scroll page
- browser_wait: Wait for element to appear
- browser_execute_js: Execute JavaScript
- browser_get_content: Get page content
- browser_screenshot: Take page screenshot
- browser_list_tabs / browser_switch_tab / browser_new_tab: Tab management
- view_image: View/analyze local image
- browser_close: Close browser
"""

from .base import build_detail

# ==================== Tool Definitions ====================

BROWSER_TOOLS = [
    # ---------- browser_open ---------- (merged with browser_status)
    {
        "name": "browser_open",
        "category": "Browser",
        "description": "Launch browser OR check browser status. Always returns current state (is_open, url, title, tab_count). If browser is already running, returns status without restarting. If not running, starts it. Call this before any browser operation to ensure browser is ready. Browser state resets on service restart.",
        "detail": build_detail(
            summary="Launch browser or check browser status. Always returns current state (is_open, URL, title, tab count).",
            scenarios=[
                "Confirm browser status before starting a web automation task",
                "Launch the browser",
                "Check whether the browser is running normally",
            ],
            params_desc={
                "visible": "True=show browser window (visible to user), False=run in background (hidden)",
            },
            notes=[
                "⚠️ Call this tool before each browser task to confirm status",
                "If the browser is already running, returns the current status without relaunching",
                "The browser closes on service restart; do not assume it is open",
                "Shows the browser window by default",
            ],
        ),
        "triggers": [
            "Before any browser operation",
            "When starting web automation tasks",
            "When checking if browser is running",
        ],
        "prerequisites": [],
        "warnings": [
            "Browser state resets on service restart - never assume it's open from history",
        ],
        "examples": [
            {
                "scenario": "Check browser status and launch",
                "params": {},
                "expected": "Returns {is_open: true/false, url: '...', title: '...', tab_count: N}. Starts browser if not running.",
            },
            {
                "scenario": "Launch visible browser",
                "params": {"visible": True},
                "expected": "Browser window opens and is visible to user, returns status",
            },
            {
                "scenario": "Launch in background mode",
                "params": {"visible": False},
                "expected": "Browser runs in background without visible window, returns status",
            },
        ],
        "related_tools": [
            {
                "name": "browser_navigate",
                "relation": "Navigate to target URL after opening (for search tasks, pass URL params directly)",
            },
            {"name": "browser_click", "relation": "Click page elements to interact"},
            {"name": "browser_close", "relation": "Close after use"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "visible": {
                    "type": "boolean",
                    "description": "True=show browser window, False=run in background. Default True",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    # ---------- browser_navigate ----------
    {
        "name": "browser_navigate",
        "category": "Browser",
        "description": "Navigate browser to URL. **Recommended for search tasks** - directly use URL with query params (e.g. https://www.baidu.com/s?wd=keyword, https://image.baidu.com/search/index?tn=baiduimage&word=keyword, https://www.google.com/search?q=keyword). Auto-starts browser if not running.",
        "detail": build_detail(
            summary="Navigate to the specified URL. For search tasks, pass URL params directly.",
            scenarios=[
                "Search tasks: use URL params directly (e.g. baidu.com/s?wd=keyword)",
                "Open a webpage to view content",
                "First step of a web automation task",
                "Switch to a new page",
            ],
            params_desc={
                "url": "Full URL to visit (must include protocol, e.g. https://)",
            },
            workflow_steps=[
                "Call this tool to navigate to the target page",
                "Wait for the page to load",
                "Use browser_get_content to get content or browser_screenshot to capture a screenshot",
            ],
            notes=[
                "⚠️ For search tasks, prefer this tool and include search params directly in the URL",
                "Common search URL templates: Baidu search https://www.baidu.com/s?wd=keyword",
                "Baidu image https://image.baidu.com/search/index?tn=baiduimage&word=keyword",
                "Google https://www.google.com/search?q=keyword",
                "The browser is launched automatically if not running",
                "The URL must include the protocol (http:// or https://)",
            ],
        ),
        "triggers": [
            "When user asks to search for something on the web",
            "When user asks to open a webpage",
            "When starting web automation task with a known URL",
        ],
        "prerequisites": [],
        "warnings": [],
        "examples": [
            {
                "scenario": "Open a search engine",
                "params": {"url": "https://www.google.com"},
                "expected": "Browser navigates to Google homepage",
            },
            {
                "scenario": "Open a local file",
                "params": {"url": "file:///C:/Users/test.html"},
                "expected": "Browser opens local HTML file",
            },
        ],
        "related_tools": [
            {"name": "browser_get_content", "relation": "Get page text content after navigation"},
            {"name": "browser_click", "relation": "Click page elements after navigation"},
            {"name": "browser_screenshot", "relation": "Take a screenshot after navigation"},
            {"name": "view_image", "relation": "View screenshot contents to verify page state"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to visit (must include protocol). For search tasks, include params directly in the URL",
                },
            },
            "required": ["url"],
        },
    },
    # ---------- browser_get_content ----------
    {
        "name": "browser_get_content",
        "category": "Browser",
        "description": "Extract page content and element text from current webpage. When you need to: (1) Read page information, (2) Get element values, (3) Scrape data, (4) Verify page content.",
        "detail": build_detail(
            summary="Get page content (text or HTML).",
            scenarios=[
                "Read page information",
                "Get element values",
                "Scrape data",
                "Verify page content",
            ],
            params_desc={
                "selector": "Element selector (optional; omit to get the entire page)",
                "format": "Return format: text (plain text, default) or html (HTML source)",
            },
            notes=[
                "No selector: get the full page text",
                "With selector: get text of the specific element",
                "format defaults to text; specify html to get the HTML source",
            ],
        ),
        "triggers": [
            "When reading page information",
            "When extracting data from webpage",
            "When verifying page content after navigation",
        ],
        "prerequisites": [
            "Page must be loaded (browser_navigate called)",
        ],
        "warnings": [],
        "examples": [
            {
                "scenario": "Get the full page content",
                "params": {},
                "expected": "Returns full page text content",
            },
            {
                "scenario": "Get content of a specific element",
                "params": {"selector": ".article-body"},
                "expected": "Returns text content of article body",
            },
            {
                "scenario": "Get page HTML source",
                "params": {"format": "html"},
                "expected": "Returns full page HTML content",
            },
        ],
        "related_tools": [
            {"name": "browser_navigate", "relation": "load page before getting content"},
            {"name": "browser_screenshot", "relation": "alternative - visual capture"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Element selector (optional; omit to get the entire page)",
                },
                "format": {
                    "type": "string",
                    "enum": ["text", "html"],
                    "description": "Return format: text (plain text, default) or html (HTML source)",
                    "default": "text",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum characters to return, default 12000. Excess is saved to an overflow file, which can be read page-by-page via read_file",
                    "default": 12000,
                },
            },
            "required": [],
        },
    },
    # ---------- browser_screenshot ----------
    {
        "name": "browser_screenshot",
        "category": "Browser",
        "description": "Capture browser page screenshot (webpage content only, not desktop). When you need to: (1) Show page state to user, (2) Document web results, (3) Debug page issues. For desktop/application screenshots, use desktop_screenshot instead.",
        "detail": build_detail(
            summary="Capture a screenshot of the current page.",
            scenarios=[
                "Show page state to the user",
                "Document the results of a web operation",
                "Debug page issues",
            ],
            params_desc={
                "full_page": "Whether to capture the full page (including scrollable areas); defaults to False (visible area only)",
                "path": "Save path (optional; auto-generated if omitted)",
            },
            notes=[
                "Captures browser page content only",
                "Use desktop_screenshot for the desktop or other applications",
                "full_page=True captures the full page (including content that requires scrolling)",
                "In IM scenarios, the screenshot is saved locally on the server; use `deliver_artifacts` to deliver it to the user",
            ],
        ),
        "triggers": [
            "When user asks to see the webpage",
            "When documenting web automation results",
            "When debugging page display issues",
        ],
        "prerequisites": [
            "Page must be loaded (browser_navigate called)",
        ],
        "warnings": [],
        "examples": [
            {
                "scenario": "Capture the current page",
                "params": {},
                "expected": "Saves screenshot with auto-generated filename",
            },
            {
                "scenario": "Capture the full page",
                "params": {"full_page": True},
                "expected": "Saves full-page screenshot including scrollable content",
            },
            {
                "scenario": "Save to a specified path",
                "params": {"path": "C:/screenshots/result.png"},
                "expected": "Saves screenshot to specified path",
            },
        ],
        "related_tools": [
            {"name": "desktop_screenshot", "relation": "alternative for desktop apps"},
            {
                "name": "deliver_artifacts",
                "relation": "deliver the screenshot as an attachment (with receipts)",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "full_page": {
                    "type": "boolean",
                    "description": "Whether to capture the full page (including scrollable areas); defaults to visible area only",
                    "default": False,
                },
                "path": {"type": "string", "description": "Save path (optional; auto-generated if omitted)"},
            },
            "required": [],
        },
    },
    # ---------- browser_click ----------
    {
        "name": "browser_click",
        "category": "Browser",
        "description": "Click an element on the current page. Use CSS selector or visible text to identify the target element.",
        "detail": build_detail(
            summary="Click an element on the page. Supports CSS selector or visible text targeting.",
            scenarios=[
                "Click buttons and links",
                "Select dropdown menu options",
                "Click form controls",
            ],
            params_desc={
                "selector": "CSS selector (e.g. 'button.submit', '#login-btn', 'a.product-link')",
                "text": "Visible text of the element (e.g. 'Submit', 'Log in'). At least one of selector or text is required",
            },
            notes=[
                "At least one of selector or text must be provided",
                "Prefer selector for precise targeting; text is for fuzzy matching",
                "Before clicking, verify the element exists using browser_get_content",
            ],
        ),
        "triggers": [
            "When user asks to click a button or link",
            "When interacting with page elements",
        ],
        "prerequisites": ["Page must be loaded"],
        "warnings": [],
        "examples": [
            {
                "scenario": "Click the submit button",
                "params": {"selector": "button[type='submit']"},
                "expected": "Clicks the submit button",
            },
            {
                "scenario": "Click a text link",
                "params": {"text": "Log in"},
                "expected": "Clicks element containing text 'Log in'",
            },
        ],
        "related_tools": [
            {"name": "browser_get_content", "relation": "Verify page elements before clicking"},
            {"name": "browser_screenshot", "relation": "Capture a screenshot after clicking to verify"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector (e.g. 'button.submit', '#login-btn')",
                },
                "text": {
                    "type": "string",
                    "description": "Visible text of the element (e.g. 'Submit', 'Log in')",
                },
            },
            "required": [],
        },
    },
    # ---------- browser_type ----------
    {
        "name": "browser_type",
        "category": "Browser",
        "description": "Type text into an input field on the current page. Identifies the field by CSS selector.",
        "detail": build_detail(
            summary="Type text into an input field.",
            scenarios=[
                "Fill in a search box",
                "Fill form fields (username, password, email, etc.)",
                "Enter content in a text area",
            ],
            params_desc={
                "selector": "CSS selector of the input field (e.g. 'input[name=\"username\"]', '#search-box')",
                "text": "Text to input",
                "clear": "Whether to clear the input field first (default True)",
            },
            notes=[
                "Clears the input field before typing by default",
                "Set clear=False to append text",
            ],
        ),
        "triggers": [
            "When filling form fields",
            "When typing in search boxes",
        ],
        "prerequisites": ["Page must be loaded"],
        "warnings": [],
        "examples": [
            {
                "scenario": "Type into a search box",
                "params": {"selector": "#search-box", "text": "mechanical keyboard"},
                "expected": "Types 'mechanical keyboard' into the search box",
            },
        ],
        "related_tools": [
            {"name": "browser_click", "relation": "You may need to click a submit button after typing"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the input field",
                },
                "text": {
                    "type": "string",
                    "description": "Text to input",
                },
                "clear": {
                    "type": "boolean",
                    "description": "Whether to clear the input field first (default True)",
                    "default": True,
                },
            },
            "required": ["selector", "text"],
        },
    },
    # ---------- browser_scroll ----------
    {
        "name": "browser_scroll",
        "category": "Browser",
        "description": "Scroll the page up or down by a specified amount of pixels.",
        "detail": build_detail(
            summary="Scroll the page.",
            scenarios=[
                "View content below the fold",
                "Scroll to a specific area",
                "Browse long pages",
            ],
            params_desc={
                "direction": "Scroll direction: 'up' or 'down' (default 'down')",
                "amount": "Number of pixels to scroll (default 500)",
            },
        ),
        "triggers": [
            "When content is below the visible area",
            "When browsing long pages",
        ],
        "prerequisites": ["Page must be loaded"],
        "warnings": [],
        "examples": [
            {
                "scenario": "Scroll down",
                "params": {"direction": "down", "amount": 500},
                "expected": "Scrolls down 500 pixels",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction",
                    "default": "down",
                },
                "amount": {
                    "type": "integer",
                    "description": "Number of pixels to scroll",
                    "default": 500,
                },
            },
            "required": [],
        },
    },
    # ---------- browser_wait ----------
    {
        "name": "browser_wait",
        "category": "Browser",
        "description": "Wait for a specific element to appear on the page. Useful after navigation or clicks that trigger dynamic content loading.",
        "detail": build_detail(
            summary="Wait for a page element to appear. Useful for dynamically loaded content.",
            scenarios=[
                "Wait for a page to finish loading",
                "Wait for an element to appear after an AJAX request",
                "Wait for a popup to appear",
            ],
            params_desc={
                "selector": "CSS selector of the element to wait for",
                "timeout": "Timeout in milliseconds, default 30000 (30s)",
            },
        ),
        "triggers": [
            "After navigation when page uses dynamic loading",
            "After click that triggers AJAX content",
        ],
        "prerequisites": ["Page must be loaded"],
        "warnings": [],
        "examples": [
            {
                "scenario": "Wait for search results to load",
                "params": {"selector": ".search-results", "timeout": 10000},
                "expected": "Waits up to 10s for search results to appear",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the element to wait for",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in milliseconds, default 30000",
                    "default": 30000,
                },
            },
            "required": ["selector"],
        },
    },
    # ---------- browser_execute_js ----------
    {
        "name": "browser_execute_js",
        "category": "Browser",
        "description": "Execute JavaScript code on the current page. Returns the evaluation result.",
        "detail": build_detail(
            summary="Execute JavaScript code on the current page.",
            scenarios=[
                "Get specific data from the page",
                "Modify the page state",
                "Call JavaScript functions on the page",
                "Get DOM element attributes",
            ],
            params_desc={
                "script": "JavaScript code to execute",
            },
            notes=[
                "The code runs in the page context",
                "Can return serializable results",
            ],
        ),
        "triggers": [
            "When extracting specific data from page DOM",
            "When no built-in tool covers the needed operation",
        ],
        "prerequisites": ["Page must be loaded"],
        "warnings": ["Be careful with destructive JS operations"],
        "examples": [
            {
                "scenario": "Get the page title",
                "params": {"script": "document.title"},
                "expected": "Returns the page title",
            },
            {
                "scenario": "Get all links",
                "params": {
                    "script": "Array.from(document.querySelectorAll('a')).map(a => ({text: a.textContent.trim(), href: a.href})).slice(0, 20)"
                },
                "expected": "Returns first 20 links with text and href",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "JavaScript code to execute",
                },
            },
            "required": ["script"],
        },
    },
    # ---------- browser_list_tabs ----------
    {
        "name": "browser_list_tabs",
        "category": "Browser",
        "description": "List all open browser tabs with their URLs and titles.",
        "detail": build_detail(
            summary="List all open browser tabs.",
            scenarios=["See which pages are currently open", "Locate the target tab in multi-tab operations"],
        ),
        "triggers": ["When managing multiple tabs"],
        "prerequisites": ["Browser must be running"],
        "warnings": [],
        "examples": [
            {"scenario": "List all tabs", "params": {}, "expected": "Returns list of tabs"},
        ],
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    # ---------- browser_switch_tab ----------
    {
        "name": "browser_switch_tab",
        "category": "Browser",
        "description": "Switch to a specific browser tab by index (0-based).",
        "detail": build_detail(
            summary="Switch to the tab at the specified index (0-based).",
            scenarios=["Switch pages in multi-tab operations"],
            params_desc={"index": "Tab index (0-based)"},
        ),
        "triggers": ["When switching between tabs"],
        "prerequisites": ["Browser must be running with multiple tabs"],
        "warnings": [],
        "examples": [
            {
                "scenario": "Switch to the second tab",
                "params": {"index": 1},
                "expected": "Switches to tab at index 1",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": "Tab index (0-based)",
                    "default": 0,
                },
            },
            "required": ["index"],
        },
    },
    # ---------- browser_new_tab ----------
    {
        "name": "browser_new_tab",
        "category": "Browser",
        "description": "Open a new browser tab, optionally navigating to a URL.",
        "detail": build_detail(
            summary="Open a new tab, optionally navigating to a specified URL.",
            scenarios=["Open a link in a new tab", "Keep the current page while viewing other content"],
            params_desc={"url": "URL to open in the new tab (optional; opens a blank page if omitted)"},
        ),
        "triggers": ["When opening a link in a new tab"],
        "prerequisites": ["Browser must be running"],
        "warnings": [],
        "examples": [
            {
                "scenario": "Open a page in a new tab",
                "params": {"url": "https://www.baidu.com"},
                "expected": "Opens Baidu in new tab",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open (optional)"},
            },
            "required": [],
        },
    },
    # ---------- view_image ----------
    {
        "name": "view_image",
        "category": "Browser",
        "description": "View/analyze a local image file. Load the image and send it to the LLM for visual understanding. Use this when you need to: (1) Verify browser screenshots show the expected content, (2) Analyze any local image file, (3) Understand what's in an image before deciding next steps. The image content will be embedded in the tool result so the LLM can SEE it directly.",
        "detail": build_detail(
            summary="View/analyze a local image file. Loads the image and embeds it into the tool result so the LLM can see the image content directly.",
            scenarios=[
                "Screenshot verification: after a screenshot, view its contents to confirm whether the page state matches expectations",
                "Analyze any local image file",
                "Understand image content before making a decision",
            ],
            params_desc={
                "path": "Image file path (supports png/jpg/jpeg/gif/webp)",
                "question": "Optional, a specific question about the image (e.g. 'How many search results are there?')",
            },
            notes=[
                "⚠️ Important: after browser_screenshot, use this tool to view the screenshot when you need to confirm page content",
                "Supported formats: PNG, JPEG, GIF, WebP",
                "Images are automatically scaled to fit LLM context limits",
                "If the current model does not support vision, a VL model is used to generate a textual description",
            ],
        ),
        "triggers": [
            "When you need to verify what a screenshot actually shows",
            "After browser_screenshot, to check if the page state matches expectations",
            "When analyzing any local image file",
            "When user asks to look at or describe an image",
        ],
        "prerequisites": [],
        "warnings": [],
        "examples": [
            {
                "scenario": "Verify a browser screenshot",
                "params": {"path": "data/screenshots/screenshot_20260224_015625.png"},
                "expected": "Returns the image embedded in tool result, LLM can see and analyze the page content",
            },
            {
                "scenario": "Image analysis with a question",
                "params": {
                    "path": "data/screenshots/screenshot.png",
                    "question": "Does the page show search results? What is the search keyword?",
                },
                "expected": "LLM sees the image and can answer the specific question",
            },
        ],
        "related_tools": [
            {
                "name": "browser_screenshot",
                "relation": "take screenshot first, then view_image to analyze",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Image file path (supports png/jpg/jpeg/gif/webp/bmp)",
                },
                "question": {
                    "type": "string",
                    "description": "Specific question about the image (optional; leave empty to return the image for the LLM to analyze on its own)",
                },
            },
            "required": ["path"],
        },
    },
    # ---------- browser_close ----------
    {
        "name": "browser_close",
        "category": "Browser",
        "description": "Close the browser and release resources. Call when browser automation is complete and no longer needed. This frees memory and system resources.",
        "detail": build_detail(
            summary="Close the browser and release resources.",
            scenarios=[
                "After all browser tasks are complete",
                "When system resources need to be released",
                "When the browser needs to be restarted (close before reopening)",
            ],
            notes=[
                "After closing, you must call browser_open again to use the browser",
                "All tabs will be closed",
            ],
        ),
        "triggers": [
            "When browser automation tasks are completed",
            "When user explicitly asks to close browser",
            "When freeing system resources",
        ],
        "prerequisites": [],
        "warnings": [
            "All open tabs and pages will be closed",
        ],
        "examples": [
            {
                "scenario": "Close browser after task completion",
                "params": {},
                "expected": "Browser closes and resources are freed",
            },
        ],
        "related_tools": [
            {"name": "browser_open", "relation": "reopen browser after closing"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]
