"""
Browser 工具定义

包含浏览器自动化相关的工具（遵循 tool-definition-spec.md 规范，全部基于 Playwright）：
- browser_open: 启动浏览器 + 状态查询
- browser_navigate: 导航到 URL（搜索类任务推荐直接拼 URL 参数）
- browser_click: 点击页面元素
- browser_type: 输入文本
- browser_scroll: 滚动页面
- browser_wait: 等待元素出现
- browser_execute_js: 执行 JavaScript
- browser_get_content: 获取页面内容
- browser_screenshot: 截取页面截图
- browser_list_tabs / browser_switch_tab / browser_new_tab: 标签页管理
- view_image: 查看/分析本地图片
- browser_close: 关闭浏览器
"""

from .base import build_detail

# ==================== 工具定义 ====================

BROWSER_TOOLS = [
    # ---------- browser_open ---------- (合并了 browser_status)
    {
        "name": "browser_open",
        "category": "Browser",
        "description": "Launch browser OR check browser status. Always returns current state (is_open, url, title, tab_count). If browser is already running, returns status without restarting. If not running, starts it. Call this before any browser operation to ensure browser is ready. Browser state resets on service restart.",
        "detail": build_detail(
            summary="启动浏览器或检查浏览器状态。始终返回当前状态（是否打开、URL、标题、tab 数）。",
            scenarios=[
                "开始 Web 自动化任务前确认浏览器状态",
                "启动浏览器",
                "检查浏览器是否正常运行",
            ],
            params_desc={
                "visible": "True=显示浏览器窗口（用户可见），False=后台运行（不可见）",
            },
            notes=[
                "⚠️ 每次浏览器任务前建议调用此工具确认状态",
                "如果浏览器已在运行，直接返回当前状态，不会重复启动",
                "服务重启后浏览器会关闭，不能假设已打开",
                "默认显示浏览器窗口",
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
                "scenario": "检查浏览器状态并启动",
                "params": {},
                "expected": "Returns {is_open: true/false, url: '...', title: '...', tab_count: N}. Starts browser if not running.",
            },
            {
                "scenario": "启动可见浏览器",
                "params": {"visible": True},
                "expected": "Browser window opens and is visible to user, returns status",
            },
            {
                "scenario": "后台模式启动",
                "params": {"visible": False},
                "expected": "Browser runs in background without visible window, returns status",
            },
        ],
        "related_tools": [
            {"name": "browser_navigate", "relation": "打开后导航到目标 URL（搜索任务推荐直接拼 URL 参数）"},
            {"name": "browser_click", "relation": "点击页面元素进行交互"},
            {"name": "browser_close", "relation": "使用完毕后关闭"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "visible": {
                    "type": "boolean",
                    "description": "True=显示浏览器窗口, False=后台运行。默认 True",
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
            summary="导航到指定 URL。搜索类任务推荐直接拼 URL 参数。",
            scenarios=[
                "搜索类任务：直接用 URL 参数（如 baidu.com/s?wd=关键词）",
                "打开网页查看内容",
                "Web 自动化任务的第一步",
                "切换到新页面",
            ],
            params_desc={
                "url": "要访问的完整 URL（必须包含协议，如 https://）",
            },
            workflow_steps=[
                "调用此工具导航到目标页面",
                "等待页面加载",
                "使用 browser_get_content 获取内容 或 browser_screenshot 截图",
            ],
            notes=[
                "⚠️ 搜索类任务优先用此工具，直接在 URL 中带搜索参数",
                "常用搜索 URL 模板：百度搜索 https://www.baidu.com/s?wd=关键词",
                "百度图片 https://image.baidu.com/search/index?tn=baiduimage&word=关键词",
                "Google https://www.google.com/search?q=keyword",
                "如果浏览器未启动会自动启动",
                "URL 必须包含协议（http:// 或 https://）",
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
                "scenario": "打开搜索引擎",
                "params": {"url": "https://www.google.com"},
                "expected": "Browser navigates to Google homepage",
            },
            {
                "scenario": "打开本地文件",
                "params": {"url": "file:///C:/Users/test.html"},
                "expected": "Browser opens local HTML file",
            },
        ],
        "related_tools": [
            {"name": "browser_get_content", "relation": "导航后获取页面文本内容"},
            {"name": "browser_click", "relation": "导航后点击页面元素"},
            {"name": "browser_screenshot", "relation": "导航后截图"},
            {"name": "view_image", "relation": "截图后查看图片内容，验证页面状态"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要访问的 URL（必须包含协议）。搜索类任务直接在 URL 中带参数"},
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
            summary="获取页面内容（文本或 HTML）。",
            scenarios=[
                "读取页面信息",
                "获取元素值",
                "抓取数据",
                "验证页面内容",
            ],
            params_desc={
                "selector": "元素选择器（可选，不填则获取整个页面）",
                "format": "返回格式：text（纯文本，默认）或 html（HTML 源码）",
            },
            notes=[
                "不指定 selector：获取整个页面文本",
                "指定 selector：获取特定元素的文本",
                "format 默认为 text，如需 HTML 源码请指定为 html",
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
                "scenario": "获取整个页面内容",
                "params": {},
                "expected": "Returns full page text content",
            },
            {
                "scenario": "获取特定元素内容",
                "params": {"selector": ".article-body"},
                "expected": "Returns text content of article body",
            },
            {
                "scenario": "获取页面 HTML 源码",
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
                    "description": "元素选择器（可选，不填则获取整个页面）",
                },
                "format": {
                    "type": "string",
                    "enum": ["text", "html"],
                    "description": "返回格式：text（纯文本，默认）或 html（HTML 源码）",
                    "default": "text",
                },
                "max_length": {
                    "type": "integer",
                    "description": "最大返回字符数，默认 12000。超出部分保存到溢出文件，可用 read_file 分页读取",
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
            summary="截取当前页面截图。",
            scenarios=[
                "向用户展示页面状态",
                "记录网页操作结果",
                "调试页面问题",
            ],
            params_desc={
                "full_page": "是否截取整个页面（包含滚动区域），默认 False 只截取可视区域",
                "path": "保存路径（可选，不填自动生成）",
            },
            notes=[
                "仅截取浏览器页面内容",
                "如需截取桌面或其他应用，请使用 desktop_screenshot",
                "full_page=True 会截取页面的完整内容（包含需要滚动才能看到的部分）",
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
                "scenario": "截取当前页面",
                "params": {},
                "expected": "Saves screenshot with auto-generated filename",
            },
            {
                "scenario": "截取完整页面",
                "params": {"full_page": True},
                "expected": "Saves full-page screenshot including scrollable content",
            },
            {
                "scenario": "保存到指定路径",
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
                    "description": "是否截取整个页面（包含滚动区域），默认只截取可视区域",
                    "default": False,
                },
                "path": {"type": "string", "description": "保存路径（可选，不填自动生成）"},
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
            summary="点击页面上的元素。支持 CSS 选择器或可见文本定位。",
            scenarios=[
                "点击按钮、链接",
                "选择下拉菜单选项",
                "点击表单控件",
            ],
            params_desc={
                "selector": "CSS 选择器（如 'button.submit', '#login-btn', 'a.product-link'）",
                "text": "元素的可见文本（如 '提交', '登录'）。selector 和 text 至少提供一个",
            },
            notes=[
                "selector 和 text 至少提供一个",
                "优先使用 selector 精确定位，text 用于模糊匹配",
                "点击前建议先用 browser_get_content 确认元素存在",
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
                "scenario": "点击提交按钮",
                "params": {"selector": "button[type='submit']"},
                "expected": "Clicks the submit button",
            },
            {
                "scenario": "点击文本链接",
                "params": {"text": "登录"},
                "expected": "Clicks element containing text '登录'",
            },
        ],
        "related_tools": [
            {"name": "browser_get_content", "relation": "点击前确认页面元素"},
            {"name": "browser_screenshot", "relation": "点击后截图验证"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS 选择器（如 'button.submit', '#login-btn'）",
                },
                "text": {
                    "type": "string",
                    "description": "元素的可见文本（如 '提交', '登录'）",
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
            summary="在输入框中输入文本。",
            scenarios=[
                "填写搜索框",
                "填写表单字段（用户名、密码、邮箱等）",
                "在文本区域输入内容",
            ],
            params_desc={
                "selector": "输入框的 CSS 选择器（如 'input[name=\"username\"]', '#search-box'）",
                "text": "要输入的文本",
                "clear": "是否先清空输入框（默认 True）",
            },
            notes=[
                "默认会先清空输入框再输入",
                "设置 clear=False 可追加文本",
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
                "scenario": "在搜索框输入",
                "params": {"selector": "#search-box", "text": "机械键盘"},
                "expected": "Types '机械键盘' into the search box",
            },
        ],
        "related_tools": [
            {"name": "browser_click", "relation": "输入后可能需要点击提交按钮"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "输入框的 CSS 选择器",
                },
                "text": {
                    "type": "string",
                    "description": "要输入的文本",
                },
                "clear": {
                    "type": "boolean",
                    "description": "是否先清空输入框（默认 True）",
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
            summary="滚动页面。",
            scenarios=[
                "查看页面下方内容",
                "滚动到特定区域",
                "浏览长页面",
            ],
            params_desc={
                "direction": "滚动方向：'up' 或 'down'（默认 'down'）",
                "amount": "滚动像素数（默认 500）",
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
                "scenario": "向下滚动",
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
                    "description": "滚动方向",
                    "default": "down",
                },
                "amount": {
                    "type": "integer",
                    "description": "滚动像素数",
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
            summary="等待页面元素出现。适用于动态加载内容的场景。",
            scenarios=[
                "等待页面加载完成",
                "等待 AJAX 请求完成后元素出现",
                "等待弹窗出现",
            ],
            params_desc={
                "selector": "要等待的元素的 CSS 选择器",
                "timeout": "超时时间（毫秒），默认 30000（30秒）",
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
                "scenario": "等待搜索结果加载",
                "params": {"selector": ".search-results", "timeout": 10000},
                "expected": "Waits up to 10s for search results to appear",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "要等待的元素的 CSS 选择器",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时时间（毫秒），默认 30000",
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
            summary="在当前页面执行 JavaScript 代码。",
            scenarios=[
                "获取页面上的特定数据",
                "修改页面状态",
                "调用页面上的 JavaScript 函数",
                "获取 DOM 元素属性",
            ],
            params_desc={
                "script": "要执行的 JavaScript 代码",
            },
            notes=[
                "代码在页面上下文中执行",
                "可以返回序列化的结果",
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
                "scenario": "获取页面标题",
                "params": {"script": "document.title"},
                "expected": "Returns the page title",
            },
            {
                "scenario": "获取所有链接",
                "params": {"script": "Array.from(document.querySelectorAll('a')).map(a => ({text: a.textContent.trim(), href: a.href})).slice(0, 20)"},
                "expected": "Returns first 20 links with text and href",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "要执行的 JavaScript 代码",
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
            summary="列出所有打开的浏览器标签页。",
            scenarios=["查看当前打开了哪些页面", "在多标签操作中定位目标标签"],
        ),
        "triggers": ["When managing multiple tabs"],
        "prerequisites": ["Browser must be running"],
        "warnings": [],
        "examples": [
            {"scenario": "列出所有标签", "params": {}, "expected": "Returns list of tabs"},
        ],
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    # ---------- browser_switch_tab ----------
    {
        "name": "browser_switch_tab",
        "category": "Browser",
        "description": "Switch to a specific browser tab by index (0-based).",
        "detail": build_detail(
            summary="切换到指定索引的标签页（从 0 开始）。",
            scenarios=["在多标签操作中切换页面"],
            params_desc={"index": "标签页索引（从 0 开始）"},
        ),
        "triggers": ["When switching between tabs"],
        "prerequisites": ["Browser must be running with multiple tabs"],
        "warnings": [],
        "examples": [
            {"scenario": "切换到第二个标签", "params": {"index": 1}, "expected": "Switches to tab at index 1"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "标签页索引（从 0 开始）", "default": 0},
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
            summary="打开新标签页，可选导航到指定 URL。",
            scenarios=["需要在新标签页中打开链接", "保留当前页面同时查看其他内容"],
            params_desc={"url": "要在新标签页打开的 URL（可选，不填则打开空白页）"},
        ),
        "triggers": ["When opening a link in a new tab"],
        "prerequisites": ["Browser must be running"],
        "warnings": [],
        "examples": [
            {"scenario": "新标签打开页面", "params": {"url": "https://www.baidu.com"}, "expected": "Opens Baidu in new tab"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要打开的 URL（可选）"},
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
            summary="查看/分析本地图片文件。将图片加载并嵌入到工具结果中，让 LLM 能直接看到图片内容。",
            scenarios=[
                "截图验证：截图后查看截图内容，确认页面状态是否符合预期",
                "分析任意本地图片文件",
                "在决策前理解图片内容",
            ],
            params_desc={
                "path": "图片文件路径（支持 png/jpg/jpeg/gif/webp）",
                "question": "可选，关于图片的具体问题（如'搜索结果有多少条？'）",
            },
            notes=[
                "⚠️ 重要：browser_screenshot 截图后，如果需要确认页面内容，一定要用此工具查看截图",
                "支持格式: PNG, JPEG, GIF, WebP",
                "图片会被自动缩放以适配 LLM 上下文限制",
                "如果当前模型不支持视觉，将使用 VL 模型生成文字描述",
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
                "scenario": "验证浏览器截图",
                "params": {"path": "data/screenshots/screenshot_20260224_015625.png"},
                "expected": "Returns the image embedded in tool result, LLM can see and analyze the page content",
            },
            {
                "scenario": "带问题的图片分析",
                "params": {
                    "path": "data/screenshots/screenshot.png",
                    "question": "页面是否显示了搜索结果？搜索关键词是什么？",
                },
                "expected": "LLM sees the image and can answer the specific question",
            },
        ],
        "related_tools": [
            {"name": "browser_screenshot", "relation": "take screenshot first, then view_image to analyze"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "图片文件路径（支持 png/jpg/jpeg/gif/webp/bmp）",
                },
                "question": {
                    "type": "string",
                    "description": "关于图片的具体问题（可选，留空则返回图片让 LLM 自行分析）",
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
            summary="关闭浏览器，释放资源。",
            scenarios=[
                "浏览器任务全部完成后",
                "需要释放系统资源",
                "需要重新启动浏览器（先关闭再打开）",
            ],
            notes=[
                "关闭后需要再次调用 browser_open 才能使用浏览器",
                "所有标签页都会关闭",
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
                "scenario": "任务完成后关闭浏览器",
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
