"""
Browser 工具定义

包含浏览器自动化相关的工具（遵循 tool-definition-spec.md 规范）：
- browser_task: 【推荐优先使用】智能浏览器任务
- browser_open: 启动浏览器 + 状态查询
- browser_navigate: 导航到 URL
- browser_get_content: 获取页面内容
- browser_screenshot: 截取页面截图
- browser_close: 关闭浏览器
"""

from .base import build_detail

# ==================== 工具定义 ====================

BROWSER_TOOLS = [
    # ---------- browser_task ---------- 【推荐优先使用】
    {
        "name": "browser_task",
        "category": "Browser",
        "description": "【推荐优先使用】Intelligent browser task - describe what you want to accomplish and browser-use Agent will automatically plan and execute all steps. Best for: (1) Multi-step operations like search + filter + sort, (2) Complex web interactions including clicking, typing, form filling, (3) Tasks where you're unsure of exact steps, (4) Any task requiring element interaction (clicks, inputs, tab management). For simple URL opening only, use browser_navigate.",
        "detail": build_detail(
            summary="智能浏览器任务 - 描述你想完成的任务，browser-use Agent 会自动规划和执行所有步骤。",
            scenarios=[
                "多步骤操作（如：搜索商品 → 筛选价格 → 按销量排序）",
                "复杂网页交互（如：登录 → 填表 → 提交）",
                "不确定具体步骤的任务",
                "需要智能判断和处理的场景",
                "点击按钮、输入文本、管理标签页等所有页面交互",
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
                "✅ 推荐用于所有需要点击、输入、表单填写的操作",
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
            "When clicking buttons, filling forms, or interacting with page elements",
            "When managing multiple tabs or switching between pages",
        ],
        "prerequisites": [],
        "warnings": [
            "Task description should be clear and specific",
            "Complex tasks may need higher max_steps",
        ],
        "examples": [
            {
                "scenario": "淘宝搜索筛选排序",
                "params": {
                    "task": "打开淘宝搜索机械键盘，筛选价格200-500元，按销量排序，截图发给我"
                },
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
            {
                "name": "browser_screenshot",
                "relation": "can be used after task for manual screenshot",
            },
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
            {"name": "browser_navigate", "relation": "commonly used after opening"},
            {"name": "browser_task", "relation": "recommended for complex tasks"},
            {"name": "browser_close", "relation": "close browser when done"},
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
        },
    },
    # ---------- browser_navigate ----------
    {
        "name": "browser_navigate",
        "category": "Browser",
        "description": "Navigate browser to specified URL to open a webpage. For simple URL opening only. For multi-step tasks (search + click + type), use browser_task instead. Auto-starts browser if not running.",
        "detail": build_detail(
            summary="导航到指定 URL。",
            scenarios=[
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
                "如果浏览器未启动会自动启动",
                "URL 必须包含协议（http:// 或 https://）",
                "如需与页面交互（点击、输入），请改用 browser_task",
            ],
        ),
        "triggers": [
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
            {"name": "browser_task", "relation": "recommended for multi-step tasks or page interaction"},
            {"name": "browser_get_content", "relation": "extract content after navigation"},
            {"name": "browser_screenshot", "relation": "capture page after navigation"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要访问的 URL（必须包含协议）"},
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
            "Page must be loaded (browser_navigate called or browser_task completed)",
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
            "Page must be loaded (browser_navigate called or browser_task completed)",
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
            {
                "name": "deliver_artifacts",
                "relation": "deliver the screenshot as an attachment (with receipts)",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "保存路径（可选）"},
            },
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
        },
    },
]
