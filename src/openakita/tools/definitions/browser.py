"""
Browser 工具定义

包含浏览器自动化相关的工具（遵循 tool-definition-spec.md 规范）：
- browser_task: 【推荐优先使用】智能浏览器任务
- browser_open: 启动浏览器
- browser_status: 获取浏览器状态
- browser_navigate: 导航到 URL
- browser_click: 点击元素
- browser_type: 输入文本
- browser_get_content: 获取页面内容
- browser_screenshot: 截取页面截图
- browser_list_tabs: 列出标签页
- browser_switch_tab: 切换标签页
- browser_new_tab: 新建标签页
"""

from .base import ToolBuilder, build_detail

# ==================== 工具定义 ====================

BROWSER_TOOLS = [
    # ---------- browser_task ---------- 【推荐优先使用】
    {
        "name": "browser_task",
        "category": "Browser",
        "description": "【推荐优先使用】Intelligent browser task - describe what you want to accomplish and browser-use Agent will automatically plan and execute all steps. Best for: (1) Multi-step operations like search + filter + sort, (2) Complex web interactions, (3) Tasks where you're unsure of exact steps. For simple single operations, you can use specific tools like browser_navigate/click/type.",
        "detail": build_detail(
            summary="智能浏览器任务 - 描述你想完成的任务，browser-use Agent 会自动规划和执行所有步骤。",
            scenarios=[
                "多步骤操作（如：搜索商品 → 筛选价格 → 按销量排序）",
                "复杂网页交互（如：登录 → 填表 → 提交）",
                "不确定具体步骤的任务",
                "需要智能判断和处理的场景",
            ],
            params_desc={
                "task": "要完成的任务描述，越详细越好。例如：'打开淘宝搜索机械键盘，筛选价格200-500元，按销量排序'",
                "max_steps": "最大执行步骤数，默认15步。复杂任务可以增加。",
            },
            workflow_steps=[
                "描述你想完成的任务",
                "browser-use Agent 自动分析任务",
                "自动规划执行步骤",
                "逐步执行并处理异常",
                "返回执行结果",
            ],
            notes=[
                "✅ 推荐用于多步骤、复杂的浏览器任务",
                "自动继承系统 LLM 配置，无需额外设置 API Key",
                "通过 CDP 复用已启动的浏览器",
                "任务描述要清晰具体，避免歧义",
                "复杂任务可能需要增加 max_steps",
            ],
        ),
        "triggers": [
            "When user asks to do something complex on a website",
            "When task involves multiple steps (search, filter, sort, etc.)",
            "When exact steps are unclear",
            "When task description is high-level like '帮我在淘宝上找...'",
        ],
        "prerequisites": [],
        "warnings": [
            "Task description should be clear and specific",
            "Complex tasks may need higher max_steps",
        ],
        "examples": [
            {
                "scenario": "淘宝搜索筛选排序",
                "params": {"task": "打开淘宝搜索机械键盘，筛选价格200-500元，按销量排序，截图发给我"},
                "expected": "Agent automatically: opens Taobao → searches → filters price → sorts by sales → screenshots",
            },
            {
                "scenario": "GitHub 查找项目",
                "params": {"task": "在 GitHub 找 star 数最多的 Python 项目"},
                "expected": "Agent automatically: opens GitHub → navigates to search → sorts by stars → filters Python",
            },
            {
                "scenario": "新闻搜索",
                "params": {"task": "打开百度搜索今天的科技新闻，截图给我"},
                "expected": "Agent automatically: opens Baidu → searches → takes screenshot",
            },
        ],
        "related_tools": [
            {"name": "browser_navigate", "relation": "alternative for simple URL opening"},
            {"name": "browser_click", "relation": "alternative for single click"},
            {"name": "browser_type", "relation": "alternative for single text input"},
            {"name": "browser_screenshot", "relation": "can be used after task for manual screenshot"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要完成的任务描述，例如：'打开淘宝搜索机械键盘，筛选价格200-500元，按销量排序'",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "最大执行步骤数，默认15。复杂任务可以增加。",
                    "default": 15,
                },
            },
            "required": ["task"],
        },
    },
    
    # ---------- browser_status ----------
    {
        "name": "browser_status",
        "category": "Browser",
        "description": "Check browser current state including: open status, current URL, page title, tab count. IMPORTANT: Must call before any browser task - never assume browser is open from conversation history. Browser state resets on service restart.",
        "detail": build_detail(
            summary="获取浏览器当前状态：是否打开、当前页面 URL 和标题、打开的 tab 数量。",
            scenarios=[
                "开始浏览器任务前确认状态",
                "检查当前打开的页面",
                "验证浏览器是否正常运行",
            ],
            notes=[
                "⚠️ 每次浏览器相关任务必须先调用此工具确认当前状态",
                "不能假设浏览器已打开",
                "不能依赖历史记录，服务重启后浏览器会关闭",
            ],
        ),
        "triggers": [
            "Before any browser operation",
            "When starting web automation task",
            "When checking if browser is running",
        ],
        "prerequisites": [],
        "warnings": [
            "Must call before any browser task - never assume browser is open",
            "Browser state resets on service restart",
        ],
        "examples": [
            {
                "scenario": "开始浏览器任务前检查状态",
                "params": {},
                "expected": "Returns {is_open: true/false, url: '...', title: '...', tab_count: N}",
            },
        ],
        "related_tools": [
            {"name": "browser_open", "relation": "call if status shows not running"},
            {"name": "browser_navigate", "relation": "commonly used after status check"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    
    # ---------- browser_open ----------
    {
        "name": "browser_open",
        "category": "Browser",
        "description": "Launch and initialize browser for web automation. When you need to: (1) Start web automation tasks, (2) Begin page interaction. IMPORTANT: Must check browser_status first - browser closes on service restart, never assume it's open from history.",
        "detail": build_detail(
            summary="启动浏览器。",
            scenarios=[
                "开始 Web 自动化任务",
                "需要打开网页进行交互",
            ],
            params_desc={
                "visible": "True=显示浏览器窗口（用户可见），False=后台运行（不可见）",
                "ask_user": "是否先询问用户偏好",
            },
            notes=[
                "⚠️ 服务重启后浏览器会关闭",
                "⚠️ 必须先用 browser_status 检查状态",
                "不能依赖历史记录假设浏览器已打开",
                "默认显示浏览器窗口",
            ],
        ),
        "triggers": [
            "When browser_status shows browser not running",
            "When starting web automation tasks",
        ],
        "prerequisites": [
            "Should check browser_status first to avoid opening duplicate browsers",
        ],
        "warnings": [
            "Must check browser_status first - browser closes on service restart",
            "Never assume browser is open from conversation history",
        ],
        "examples": [
            {
                "scenario": "启动可见浏览器",
                "params": {"visible": True},
                "expected": "Browser window opens and is visible to user",
            },
            {
                "scenario": "后台模式启动",
                "params": {"visible": False},
                "expected": "Browser runs in background without visible window",
            },
        ],
        "related_tools": [
            {"name": "browser_status", "relation": "should check before opening"},
            {"name": "browser_navigate", "relation": "commonly used after opening"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "visible": {
                    "type": "boolean", 
                    "description": "True=显示浏览器窗口, False=后台运行。默认 True",
                    "default": True,
                },
                "ask_user": {
                    "type": "boolean",
                    "description": "是否先询问用户偏好",
                    "default": False,
                },
            },
        },
    },
    
    # ---------- browser_navigate ----------
    {
        "name": "browser_navigate",
        "category": "Browser",
        "description": "Navigate browser to specified URL to open a webpage. For simple URL opening only. For multi-step tasks (search + filter + sort), use browser_task instead. PREREQUISITE: Must call before browser_click/type operations. Auto-starts browser if not running.",
        "detail": build_detail(
            summary="导航到指定 URL。",
            scenarios=[
                "打开网页开始交互",
                "Web 自动化任务的第一步",
                "切换到新页面",
            ],
            params_desc={
                "url": "要访问的完整 URL（必须包含协议，如 https://）",
            },
            workflow_steps=[
                "调用此工具导航到目标页面",
                "等待页面加载",
                "使用 browser_click/browser_type 与页面交互",
            ],
            notes=[
                "⚠️ 必须在 browser_click/browser_type 之前调用此工具",
                "如果浏览器未启动会自动启动",
                "URL 必须包含协议（http:// 或 https://）",
            ],
        ),
        "triggers": [
            "When user asks to open a webpage",
            "When starting web automation task",
            "Before any browser interaction (click/type)",
        ],
        "prerequisites": [],
        "warnings": [
            "Must call before browser_click/type operations",
        ],
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
            {"name": "browser_task", "relation": "recommended for multi-step tasks"},
            {"name": "browser_status", "relation": "check before for reliability"},
            {"name": "browser_click", "relation": "commonly used after"},
            {"name": "browser_type", "relation": "commonly used after"},
            {"name": "browser_new_tab", "relation": "alternative - opens in new tab"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要访问的 URL（必须包含协议）"},
            },
            "required": ["url"],
        },
    },
    
    # ---------- browser_click ----------
    {
        "name": "browser_click",
        "category": "Browser",
        "description": "Click page elements by CSS selector or text content. For single click only. For multi-step tasks (search + filter + sort), use browser_task instead. PREREQUISITE: Must use browser_navigate to open target page first.",
        "detail": build_detail(
            summary="点击页面上的元素。",
            scenarios=[
                "点击按钮和链接",
                "选择下拉选项",
                "触发页面操作",
            ],
            params_desc={
                "selector": "CSS 选择器，如 '#btn-submit', '.button-class'",
                "text": "元素文本，如 '提交', 'Submit'",
            },
            notes=[
                "⚠️ 前提条件：必须先用 browser_navigate 打开目标页面",
                "可以用 CSS 选择器或元素文本定位",
                "如果两个参数都提供，优先使用 selector",
            ],
        ),
        "triggers": [
            "When user asks to click a button or link",
            "When selecting options in a form",
            "When triggering page actions",
        ],
        "prerequisites": [
            "Must use browser_navigate to open target page first",
        ],
        "warnings": [],
        "examples": [
            {
                "scenario": "点击提交按钮（CSS 选择器）",
                "params": {"selector": "#submit-btn"},
                "expected": "Clicks the submit button",
            },
            {
                "scenario": "点击按钮（文本匹配）",
                "params": {"text": "提交"},
                "expected": "Clicks button with text '提交'",
            },
        ],
        "related_tools": [
            {"name": "browser_task", "relation": "recommended for multi-step tasks"},
            {"name": "browser_navigate", "relation": "must call before clicking"},
            {"name": "browser_type", "relation": "commonly used together for forms"},
            {"name": "desktop_click", "relation": "alternative for desktop apps"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器"},
                "text": {"type": "string", "description": "元素文本（可选）"},
            },
        },
    },
    
    # ---------- browser_type ----------
    {
        "name": "browser_type",
        "category": "Browser",
        "description": "Type text into input fields on webpage. For single text input only. For multi-step tasks (search + filter + sort), use browser_task instead. PREREQUISITE: Must use browser_navigate to open target page first. Click field first if needed for focus.",
        "detail": build_detail(
            summary="在输入框中输入文本。",
            scenarios=[
                "填写表单",
                "输入搜索词",
                "输入数据",
            ],
            params_desc={
                "selector": "输入框的 CSS 选择器",
                "text": "要输入的文本",
            },
            notes=[
                "⚠️ 前提条件：必须先用 browser_navigate 打开目标页面",
                "如果输入框没有焦点，可能需要先点击",
                "支持中文输入",
            ],
        ),
        "triggers": [
            "When filling forms on webpage",
            "When entering search queries",
            "When inputting data into text fields",
        ],
        "prerequisites": [
            "Must use browser_navigate to open target page first",
        ],
        "warnings": [],
        "examples": [
            {
                "scenario": "在搜索框输入",
                "params": {"selector": "input[name='q']", "text": "OpenAkita"},
                "expected": "Types 'OpenAkita' into search input",
            },
            {
                "scenario": "填写用户名",
                "params": {"selector": "#username", "text": "admin"},
                "expected": "Types 'admin' into username field",
            },
        ],
        "related_tools": [
            {"name": "browser_task", "relation": "recommended for multi-step tasks"},
            {"name": "browser_navigate", "relation": "must call before typing"},
            {"name": "browser_click", "relation": "may need to click field first"},
            {"name": "desktop_type", "relation": "alternative for desktop apps"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "输入框选择器"},
                "text": {"type": "string", "description": "要输入的文本"},
            },
            "required": ["selector", "text"],
        },
    },
    
    # ---------- browser_get_content ----------
    {
        "name": "browser_get_content",
        "category": "Browser",
        "description": "Extract page content and element text from current webpage. When you need to: (1) Read page information, (2) Get element values, (3) Scrape data, (4) Verify page content.",
        "detail": build_detail(
            summary="获取页面内容（文本）。",
            scenarios=[
                "读取页面信息",
                "获取元素值",
                "抓取数据",
                "验证页面内容",
            ],
            params_desc={
                "selector": "元素选择器（可选，不填则获取整个页面）",
            },
            notes=[
                "不指定 selector：获取整个页面文本",
                "指定 selector：获取特定元素的文本",
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
        ],
        "related_tools": [
            {"name": "browser_navigate", "relation": "must call before getting content"},
            {"name": "browser_screenshot", "relation": "alternative - visual capture"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "元素选择器（可选，不填则获取整个页面）"},
            },
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
                "path": "保存路径（可选，不填自动生成）",
            },
            notes=[
                "仅截取浏览器页面内容",
                "如需截取桌面或其他应用，请使用 desktop_screenshot",
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
                "scenario": "保存到指定路径",
                "params": {"path": "C:/screenshots/result.png"},
                "expected": "Saves screenshot to specified path",
            },
        ],
        "related_tools": [
            {"name": "desktop_screenshot", "relation": "alternative for desktop apps"},
            {"name": "send_to_chat", "relation": "commonly used after to send image"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "保存路径（可选）"},
            },
        },
    },
    
    # ---------- browser_list_tabs ----------
    {
        "name": "browser_list_tabs",
        "category": "Browser",
        "description": "List all open browser tabs with their index, URL and title. When you need to: (1) Check what pages are open, (2) Manage multiple tabs, (3) Find a specific tab to switch to.",
        "detail": build_detail(
            summary="列出所有打开的标签页(tabs)，返回每个 tab 的索引、URL 和标题。",
            scenarios=[
                "查看打开的页面",
                "管理多个标签页",
                "查找特定标签页",
            ],
            notes=[
                "标签页索引从 0 开始",
                "返回信息包括：索引、URL、页面标题",
            ],
        ),
        "triggers": [
            "When checking what pages are open",
            "When managing multiple browser tabs",
            "Before switching to a specific tab",
        ],
        "prerequisites": [
            "Browser must be running",
        ],
        "warnings": [],
        "examples": [
            {
                "scenario": "列出所有标签页",
                "params": {},
                "expected": "Returns list of {index, url, title} for each tab",
            },
        ],
        "related_tools": [
            {"name": "browser_switch_tab", "relation": "commonly used after to switch"},
            {"name": "browser_new_tab", "relation": "add new tab"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    
    # ---------- browser_switch_tab ----------
    {
        "name": "browser_switch_tab",
        "category": "Browser",
        "description": "Switch to a specific browser tab by index. When you need to: (1) Work with a different tab, (2) Return to previous page. Use browser_list_tabs to get tab indices.",
        "detail": build_detail(
            summary="切换到指定的标签页。",
            scenarios=[
                "切换到其他打开的页面",
                "返回之前的页面",
            ],
            params_desc={
                "index": "标签页索引（从 0 开始）",
            },
            workflow_steps=[
                "先用 browser_list_tabs 获取所有标签页",
                "使用返回的索引切换",
            ],
        ),
        "triggers": [
            "When working with multiple tabs",
            "When returning to a previously opened page",
        ],
        "prerequisites": [
            "Browser must have multiple tabs open",
            "Use browser_list_tabs to get tab indices first",
        ],
        "warnings": [],
        "examples": [
            {
                "scenario": "切换到第一个标签页",
                "params": {"index": 0},
                "expected": "Switches to first tab",
            },
            {
                "scenario": "切换到第三个标签页",
                "params": {"index": 2},
                "expected": "Switches to third tab",
            },
        ],
        "related_tools": [
            {"name": "browser_list_tabs", "relation": "use first to get indices"},
            {"name": "browser_new_tab", "relation": "add new tab instead"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "number", "description": "标签页索引（从 0 开始）"},
            },
            "required": ["index"],
        },
    },
    
    # ---------- browser_new_tab ----------
    {
        "name": "browser_new_tab",
        "category": "Browser",
        "description": "Open new browser tab and navigate to URL (keeps current page open). When you need to: (1) Open additional page without closing current, (2) Multi-task across pages. PREREQUISITE: Must confirm browser is running first with browser_status.",
        "detail": build_detail(
            summary="打开新标签页并导航到指定 URL。",
            scenarios=[
                "保留当前页面的同时打开新页面",
                "多页面同时操作",
            ],
            params_desc={
                "url": "要在新标签页打开的 URL",
            },
            notes=[
                "不会覆盖当前页面，在新标签页打开",
                "⚠️ 必须先确认浏览器已启动（用 browser_status 检查）",
            ],
        ),
        "triggers": [
            "When opening additional page without closing current",
            "When multitasking across multiple pages",
        ],
        "prerequisites": [
            "Browser must be running (check with browser_status)",
        ],
        "warnings": [
            "Must confirm browser is running first with browser_status",
        ],
        "examples": [
            {
                "scenario": "在新标签页打开文档",
                "params": {"url": "https://docs.example.com"},
                "expected": "Opens documentation in new tab, keeps current page",
            },
        ],
        "related_tools": [
            {"name": "browser_status", "relation": "check before opening new tab"},
            {"name": "browser_navigate", "relation": "alternative - replaces current page"},
            {"name": "browser_switch_tab", "relation": "switch between tabs"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要访问的 URL"},
            },
            "required": ["url"],
        },
    },
]
