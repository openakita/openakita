"""
Browser 工具定义

包含浏览器自动化相关的工具：
- browser_open: 启动浏览器
- browser_navigate: 导航到 URL
- browser_click: 点击元素
- browser_type: 输入文本
- browser_get_content: 获取页面内容
- browser_screenshot: 截取页面截图
- browser_status: 获取浏览器状态
- browser_list_tabs: 列出标签页
- browser_switch_tab: 切换标签页
- browser_new_tab: 新建标签页
"""

BROWSER_TOOLS = [
    {
        "name": "browser_open",
        "description": "Launch and initialize browser for web automation. When you need to: (1) Start web automation tasks, (2) Begin page interaction. IMPORTANT: Must check browser_status first - browser closes on service restart, never assume it's open from history.",
        "detail": """启动浏览器。

⚠️ **重要警告**：
- 服务重启后浏览器会关闭
- 必须先用 browser_status 检查状态
- 不能依赖历史记录假设浏览器已打开

**参数说明**：
- visible=True: 显示浏览器窗口（用户可见）
- visible=False: 后台运行（不可见）
- 默认显示浏览器窗口""",
        "input_schema": {
            "type": "object",
            "properties": {
                "visible": {
                    "type": "boolean", 
                    "description": "True=显示浏览器窗口, False=后台运行。默认 True",
                    "default": True
                },
                "ask_user": {
                    "type": "boolean",
                    "description": "是否先询问用户偏好",
                    "default": False
                }
            }
        }
    },
    {
        "name": "browser_navigate",
        "description": "Navigate browser to specified URL to open a webpage. When you need to: (1) Open a webpage, (2) Start web interaction. IMPORTANT: Must call this before browser_click/type operations. Auto-starts browser if not running.",
        "detail": """导航到指定 URL。

⚠️ **重要警告**：
- 使用浏览器前必须先调用此工具打开目标页面
- 然后才能使用 browser_type/browser_click 等操作
- 如果浏览器未启动会自动启动""",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要访问的 URL"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "browser_click",
        "description": "Click page elements by CSS selector or text content. When you need to: (1) Click buttons/links, (2) Select options, (3) Trigger actions. PREREQUISITE: Must use browser_navigate to open target page first.",
        "detail": """点击页面上的元素。

**前提条件**：必须先用 browser_navigate 打开目标页面

**定位方式**：
- CSS 选择器：如 '#btn-submit', '.button-class'
- 元素文本：如 '提交', 'Submit'

**适用场景**：
- 点击按钮和链接
- 选择下拉选项
- 触发页面操作""",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器"},
                "text": {"type": "string", "description": "元素文本（可选）"}
            }
        }
    },
    {
        "name": "browser_type",
        "description": "Type text into input fields on webpage. When you need to: (1) Fill forms, (2) Enter search queries, (3) Input data. PREREQUISITE: Must use browser_navigate to open target page first. Click field first if needed for focus.",
        "detail": """在输入框中输入文本。

**前提条件**：必须先用 browser_navigate 打开目标页面

**适用场景**：
- 填写表单
- 输入搜索词
- 输入数据

**注意事项**：
- 如果输入框没有焦点，可能需要先点击
- 支持中文输入""",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "输入框选择器"},
                "text": {"type": "string", "description": "要输入的文本"}
            },
            "required": ["selector", "text"]
        }
    },
    {
        "name": "browser_get_content",
        "description": "Extract page content and element text from current webpage. When you need to: (1) Read page information, (2) Get element values, (3) Scrape data, (4) Verify page content.",
        "detail": """获取页面内容（文本）。

**适用场景**：
- 读取页面信息
- 获取元素值
- 抓取数据
- 验证页面内容

**使用方式**：
- 不指定 selector：获取整个页面文本
- 指定 selector：获取特定元素的文本""",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "元素选择器（可选，不填则获取整个页面）"}
            }
        }
    },
    {
        "name": "browser_screenshot",
        "description": "Capture browser page screenshot (webpage content only, not desktop). When you need to: (1) Show page state to user, (2) Document web results, (3) Debug page issues. For desktop/application screenshots, use desktop_screenshot instead.",
        "detail": """截取当前页面截图。

**适用场景**：
- 向用户展示页面状态
- 记录网页操作结果
- 调试页面问题

**注意**：
- 仅截取浏览器页面内容
- 如需截取桌面或其他应用，请使用 desktop_screenshot""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "保存路径（可选）"}
            }
        }
    },
    {
        "name": "browser_status",
        "description": "Check browser current state including: open status, current URL, page title, tab count. IMPORTANT: Must call before any browser task - never assume browser is open from conversation history. Browser state resets on service restart.",
        "detail": """获取浏览器当前状态：是否打开、当前页面 URL 和标题、打开的 tab 数量。

⚠️ **重要警告**：
- 每次浏览器相关任务必须先调用此工具确认当前状态
- 不能假设浏览器已打开
- 不能依赖历史记录

**返回信息**：
- 浏览器是否打开
- 当前页面 URL
- 当前页面标题
- 打开的标签页数量""",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "browser_list_tabs",
        "description": "List all open browser tabs with their index, URL and title. When you need to: (1) Check what pages are open, (2) Manage multiple tabs, (3) Find a specific tab to switch to.",
        "detail": """列出所有打开的标签页(tabs)，返回每个 tab 的索引、URL 和标题。

**适用场景**：
- 查看打开的页面
- 管理多个标签页
- 查找特定标签页

**返回信息**：
- 标签页索引（从 0 开始）
- URL
- 页面标题""",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "browser_switch_tab",
        "description": "Switch to a specific browser tab by index. When you need to: (1) Work with a different tab, (2) Return to previous page. Use browser_list_tabs to get tab indices.",
        "detail": """切换到指定的标签页。

**适用场景**：
- 切换到其他打开的页面
- 返回之前的页面

**使用方法**：
1. 先用 browser_list_tabs 获取所有标签页
2. 使用返回的索引切换""",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "number", "description": "标签页索引（从 0 开始）"}
            },
            "required": ["index"]
        }
    },
    {
        "name": "browser_new_tab",
        "description": "Open new browser tab and navigate to URL (keeps current page open). When you need to: (1) Open additional page without closing current, (2) Multi-task across pages. PREREQUISITE: Must confirm browser is running first with browser_status.",
        "detail": """打开新标签页并导航到指定 URL。

**特点**：不会覆盖当前页面，在新标签页打开

**适用场景**：
- 保留当前页面的同时打开新页面
- 多页面同时操作

⚠️ **前提条件**：
- 必须先确认浏览器已启动（用 browser_status 检查）
- 不能假设浏览器状态""",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要访问的 URL"}
            },
            "required": ["url"]
        }
    },
]
