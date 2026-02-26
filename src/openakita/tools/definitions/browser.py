"""
Browser 工具定义

包含浏览器自动化相关的工具（遵循 tool-definition-spec.md 规范）：
- browser_navigate: 导航到 URL（搜索类任务推荐直接拼 URL 参数）
- browser_task: 智能浏览器任务（适合复杂交互，不适合简单搜索）
- browser_open: 启动浏览器 + 状态查询
- browser_get_content: 获取页面内容
- browser_screenshot: 截取页面截图
- view_image: 查看/分析本地图片
- browser_close: 关闭浏览器
"""

from .base import build_detail

# ==================== 工具定义 ====================

BROWSER_TOOLS = [
    # ---------- browser_task ----------
    {
        "name": "browser_task",
        "category": "Browser",
        "description": "Intelligent browser task - delegates to browser-use Agent for complex multi-step interactions. Best for: (1) Complex workflows like login + fill form + submit, (2) Tasks requiring multiple clicks and interactions on the SAME page. **NOT recommended for search tasks** - use browser_navigate with URL params instead (e.g. https://www.baidu.com/s?wd=keyword). If browser_task fails once, switch to manual steps (browser_navigate + browser_get_content).",
        "related_tools": [
            {"name": "web_search", "relation": "仅需快速获取搜索结果（无需页面交互）时改用 web_search，更快更省资源"},
            {"name": "browser_navigate", "relation": "搜索类任务优先使用，拼 URL 参数直达"},
            {
                "name": "view_image",
                "relation": "browser_task 完成后务必截图+view_image 验证结果",
            },
        ],
        "detail": build_detail(
            summary="智能浏览器任务 - 委托 browser-use Agent 自动执行复杂交互。",
            scenarios=[
                "复杂网页交互（如：登录 → 填表 → 提交）",
                "需要多次点击、选择的操作（如：筛选 → 排序 → 翻页）",
                "不确定具体步骤的复杂任务",
            ],
            params_desc={
                "task": "要完成的任务描述，越详细越好。例如：'登录后填写表单并提交'",
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
                "⚠️ 搜索类任务请不要用 browser_task！直接用 browser_navigate 拼 URL 参数更可靠",
                "⚠️ 如果 browser_task 失败 1 次，立即切换为手动分步操作",
                "适合需要多次 UI 交互的复杂场景（登录、填表、筛选等）",
                "通过 CDP 复用已启动的浏览器",
                "任务描述要清晰具体，避免歧义",
                "任务完成后用 browser_screenshot + view_image 验证结果",
            ],
        ),
        "triggers": [
            "When task involves complex multi-step UI interactions (login, form filling, etc.)",
            "When exact steps are unclear and the task requires intelligent planning",
            "When managing multiple tabs or complex page interactions",
        ],
        "prerequisites": [],
        "warnings": [
            "Do NOT use for search tasks - use browser_navigate with URL params instead",
            "If browser_task fails once, immediately switch to manual browser tools",
            "Always verify results with browser_screenshot + view_image after completion",
        ],
        "examples": [
            {
                "scenario": "淘宝筛选排序（复杂交互）",
                "params": {
                    "task": "在淘宝商品列表页筛选价格200-500元，按销量排序"
                },
                "expected": "Agent automatically: filters price → sorts by sales",
            },
            {
                "scenario": "表单填写",
                "params": {"task": "填写注册表单：用户名test，邮箱test@example.com，点击提交"},
                "expected": "Agent fills form fields and submits",
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
            {"name": "browser_navigate", "relation": "打开后导航到目标 URL（搜索任务推荐直接拼 URL 参数）"},
            {"name": "browser_task", "relation": "仅在需要复杂 UI 交互时使用"},
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
        "description": "Navigate browser to URL. **Recommended for search tasks** - directly use URL with query params (e.g. https://www.baidu.com/s?wd=keyword, https://image.baidu.com/search/index?tn=baiduimage&word=keyword, https://www.google.com/search?q=keyword). Much more reliable than browser_task for searches. Auto-starts browser if not running.",
        "detail": build_detail(
            summary="导航到指定 URL。搜索类任务推荐直接拼 URL 参数，比 browser_task 更可靠。",
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
            "When browser_task has failed - use URL params as fallback",
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
            {"name": "browser_screenshot", "relation": "导航后截图"},
            {"name": "view_image", "relation": "截图后查看图片内容，验证页面状态"},
            {"name": "browser_task", "relation": "仅在需要复杂 UI 交互（登录、填表）时使用"},
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
