"""
Browser Use MCP - 浏览器自动化模块

基于 browser-use 库实现智能浏览器自动化
https://github.com/browser-use/browser-use
Licensed under MIT License

功能:
- browser_task: 智能任务执行（推荐优先使用）
- 细粒度工具: 打开网页、截图、提取内容等

外部浏览器后端（通过 MCP 集成）:
- Chrome DevTools MCP (Google 官方): 连接用户真实 Chrome，保留登录态
- mcp-chrome 扩展: 通过 Chrome 扩展直接操作用户浏览器

随 OpenAkita 系统一起启动
"""

import asyncio
import logging
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Playwright 延迟导入 (可能未安装)
playwright = None
Browser = None
Page = None


def detect_chrome_installation() -> tuple[str | None, str | None]:
    """
    检测系统上的 Chrome 安装

    Returns:
        (executable_path, user_data_dir) - 如果找到 Chrome
        (None, None) - 如果未找到
    """
    system = platform.system()

    if system == "Windows":
        # Windows Chrome 路径
        chrome_paths = [
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
        ]
        user_data_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"

    elif system == "Darwin":  # macOS
        chrome_paths = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        user_data_dir = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"

    elif system == "Linux":
        chrome_paths = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/opt/google/chrome/chrome"),
        ]
        user_data_dir = Path.home() / ".config" / "google-chrome"

    else:
        return None, None

    # 检查 Chrome 是否存在
    for chrome_path in chrome_paths:
        if chrome_path.exists():
            if user_data_dir.exists():
                logger.info(f"[BrowserDetect] Found Chrome: {chrome_path}")
                logger.info(f"[BrowserDetect] User data dir: {user_data_dir}")
                return str(chrome_path), str(user_data_dir)
            else:
                logger.warning(
                    f"[BrowserDetect] Chrome found but user data dir missing: {user_data_dir}"
                )
                return str(chrome_path), None

    logger.info("[BrowserDetect] Chrome not found, will use Chromium")
    return None, None


def get_openakita_chrome_profile() -> str:
    """
    获取 OpenAkita 专用的 Chrome profile 目录

    这个目录独立于用户的 Chrome，可以在用户 Chrome 打开时使用。
    """
    import tempfile

    # 使用固定的目录（不是每次都创建临时目录）
    # 这样可以保持登录状态
    system = platform.system()
    if system == "Windows":
        base_dir = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    elif system == "Darwin":
        # macOS 标准应用数据目录
        base_dir = Path.home() / "Library" / "Application Support"
    else:
        # Linux / other Unix: XDG data home
        base_dir = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))

    profile_dir = base_dir / "OpenAkita" / "ChromeProfile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    return str(profile_dir)


def sync_chrome_cookies(src_user_data: str, dst_profile: str) -> bool:
    """
    同步用户 Chrome 的 cookies 到 OpenAkita profile

    只复制关键文件，保持登录状态。

    Args:
        src_user_data: 用户 Chrome 的 User Data 目录
        dst_profile: OpenAkita profile 目录

    Returns:
        是否成功
    """
    import shutil

    src_default = Path(src_user_data) / "Default"
    dst_default = Path(dst_profile) / "Default"

    if not src_default.exists():
        logger.warning(f"[CookieSync] Source Default profile not found: {src_default}")
        return False

    dst_default.mkdir(parents=True, exist_ok=True)

    # 需要复制的关键文件
    important_files = [
        "Cookies",  # 网站 cookies
        "Login Data",  # 保存的密码
        "Web Data",  # 表单自动填充
        "Preferences",  # 偏好设置
        "Secure Preferences",
        "Local State",  # 本地状态
    ]

    copied = 0
    for filename in important_files:
        src_file = src_default / filename
        if src_file.exists():
            try:
                # 复制文件（如果目标文件较旧或不存在）
                dst_file = dst_default / filename
                if not dst_file.exists() or src_file.stat().st_mtime > dst_file.stat().st_mtime:
                    shutil.copy2(src_file, dst_file)
                    copied += 1
            except Exception as e:
                logger.warning(f"[CookieSync] Failed to copy {filename}: {e}")

    # 也复制根目录的 Local State
    src_local_state = Path(src_user_data) / "Local State"
    dst_local_state = Path(dst_profile) / "Local State"
    if src_local_state.exists():
        try:
            shutil.copy2(src_local_state, dst_local_state)
        except Exception as e:
            logger.warning(f"[CookieSync] Failed to copy Local State: {e}")

    logger.info(f"[CookieSync] Synced {copied} files from user Chrome")
    return copied > 0


def detect_chrome_devtools_mcp() -> dict:
    """
    检测 Chrome DevTools MCP 是否可用

    Returns:
        {
            "available": bool,
            "npx_available": bool,
            "chrome_available": bool,
            "suggestion": str,  # 用户引导建议
        }
    """
    result = {
        "available": False,
        "npx_available": False,
        "chrome_available": False,
        "suggestion": "",
    }

    # 检查 npx 是否可用
    npx_path = shutil.which("npx")
    result["npx_available"] = npx_path is not None

    # 检查 Chrome 是否存在
    chrome_path, _ = detect_chrome_installation()
    result["chrome_available"] = chrome_path is not None

    if result["npx_available"] and result["chrome_available"]:
        result["available"] = True
        result["suggestion"] = (
            "Chrome DevTools MCP 可用。建议在 Chrome 中访问 chrome://inspect/#remote-debugging "
            "开启远程调试，以便 AI Agent 连接到您的浏览器（保留登录状态和密码管理器）。"
        )
    elif not result["npx_available"]:
        result["suggestion"] = (
            "Chrome DevTools MCP 需要 Node.js。请安装 Node.js v20.19+ 以启用此功能。"
        )
    elif not result["chrome_available"]:
        result["suggestion"] = (
            "未检测到 Chrome 浏览器。请安装 Chrome 以使用 Chrome DevTools MCP。"
        )

    return result


async def check_mcp_chrome_extension(port: int = 12306, timeout: float = 2.0) -> bool:
    """
    检测 mcp-chrome 扩展是否正在运行

    Args:
        port: mcp-chrome 默认端口
        timeout: 超时时间（秒）

    Returns:
        是否可达
    """
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            await client.get(
                f"http://127.0.0.1:{port}/mcp",
                timeout=timeout,
            )
            # 即使返回非 200 也说明端口在监听
            return True
    except Exception:
        return False


@dataclass
class BrowserTool:
    """浏览器工具定义"""

    name: str
    description: str
    arguments: dict = field(default_factory=dict)


class BrowserMCP:
    """
    Browser Use MCP Server

    基于 Playwright 的浏览器自动化服务
    """

    # MCP 格式的工具定义（用于 get_tools() 返回 MCP 格式 schema）
    # 注意：LLM API 实际使用的 schema 来自 tools/definitions/browser.py 中的 BROWSER_TOOLS，
    # 修改参数 schema 时请同步更新两处！
    TOOLS = [
        BrowserTool(
            name="browser_task",
            description="【推荐优先使用】智能浏览器任务 - 描述你想完成的任务，browser-use Agent 会自动规划和执行所有步骤。适用于多步骤操作、复杂网页交互（点击/输入/表单等）、不确定具体步骤的场景。",
            arguments={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "要完成的任务描述，例如：'打开百度搜索福建福州并截图'",
                    },
                    "max_steps": {
                        "type": "integer",
                        "description": "最大执行步骤数",
                        "default": 15,
                    },
                },
                "required": ["task"],
            },
        ),
        BrowserTool(
            name="browser_open",
            description="启动浏览器或检查状态。始终返回当前状态（is_open, url, title, tab_count）。已运行则直接返回状态不重复启动。",
            arguments={
                "type": "object",
                "properties": {
                    "visible": {
                        "type": "boolean",
                        "description": "是否显示浏览器窗口。True=用户可见(调试/演示), False=后台运行(自动化)",
                        "default": True,
                    },
                },
                "required": [],
            },
        ),
        BrowserTool(
            name="browser_navigate",
            description="导航到指定 URL (如果浏览器未启动，会自动以后台模式启动)",
            arguments={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "要访问的 URL"}},
                "required": ["url"],
            },
        ),
        BrowserTool(
            name="browser_get_content",
            description="获取页面内容 (文本或 HTML)",
            arguments={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "元素选择器 (可选，默认整个页面)",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "html"],
                        "description": "返回格式：text（纯文本，默认）或 html（HTML 源码）",
                        "default": "text",
                    },
                },
                "required": [],
            },
        ),
        BrowserTool(
            name="browser_screenshot",
            description="截取当前页面截图",
            arguments={
                "type": "object",
                "properties": {
                    "full_page": {
                        "type": "boolean",
                        "description": "是否截取整个页面（包含滚动区域）",
                        "default": False,
                    },
                    "path": {"type": "string", "description": "保存路径 (可选)"},
                },
                "required": [],
            },
        ),
        BrowserTool(
            name="browser_close",
            description="关闭浏览器，释放资源",
            arguments={"type": "object", "properties": {}, "required": []},
        ),
    ]

    # 内部保留的工具（不暴露给 LLM，但 call_tool 仍可路由到它们，供内部使用）
    _INTERNAL_TOOLS = [
        "browser_click", "browser_type", "browser_wait", "browser_scroll",
        "browser_execute_js", "browser_status", "browser_list_tabs",
        "browser_switch_tab", "browser_new_tab",
    ]

    def __init__(self, headless: bool = False, cdp_port: int = 9222, use_user_chrome: bool = True):
        """
        Args:
            headless: 是否无头模式 (默认 False，即可见模式)
            cdp_port: Chrome DevTools Protocol 端口，用于 browser-use 连接
            use_user_chrome: 是否优先使用用户安装的 Chrome（保留登录状态）
        """
        self.headless = headless
        self.cdp_port = cdp_port
        self.use_user_chrome = use_user_chrome
        self._playwright = None
        self._browser = None
        self._context = None  # 显式管理 context
        self._page: Any | None = None
        self._started = False
        self._visible = True  # 当前是否可见模式（默认可见）
        self._cdp_url: str | None = None  # CDP 连接地址
        self._using_user_chrome = False  # 标记是否正在使用用户 Chrome

        # 外部注入的 LLM 配置（由 Agent 设置）
        self._llm_config: dict | None = None

        # 检测用户 Chrome
        self._chrome_path, self._chrome_user_data = detect_chrome_installation()

    async def start(self, visible: bool = None) -> bool:
        """
        启动浏览器

        Args:
            visible: 是否可见模式。None 使用默认设置，True 显示窗口，False 后台运行
        """
        if self._started:
            # 如果已启动但模式不同，需要重启
            if visible is not None and visible != self._visible:
                logger.info(f"Browser mode change requested: visible={visible}, restarting...")
                await self.stop()
            else:
                return True

        # 确定是否 headless
        headless = not visible if visible is not None else self.headless

        # 确保 Playwright 能找到通过设置中心安装的浏览器二进制
        # install_module() 将 Chromium 下载到 ~/.openakita/modules/browser/browsers/
        import os
        from pathlib import Path

        if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
            browsers_dir = Path.home() / ".openakita" / "modules" / "browser" / "browsers"
            if browsers_dir.is_dir():
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
                logger.info(f"[Browser] Set PLAYWRIGHT_BROWSERS_PATH={browsers_dir}")

        try:
            # 延迟导入 playwright
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()

            # 尝试 0：连接已运行的 Chrome（如果以调试模式启动）
            # 用户可以用以下命令启动 Chrome：
            # chrome.exe --remote-debugging-port=9222
            try:
                import httpx

                # 检查是否有 Chrome 在监听调试端口
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"http://localhost:{self.cdp_port}/json/version", timeout=2.0
                    )
                    if response.status_code == 200:
                        logger.info(
                            f"[Browser] Found Chrome with debugging port at localhost:{self.cdp_port}"
                        )

                        # 连接到已运行的 Chrome
                        self._browser = await self._playwright.chromium.connect_over_cdp(
                            f"http://localhost:{self.cdp_port}"
                        )

                        # 获取默认 context 和 page
                        contexts = self._browser.contexts
                        if contexts:
                            self._context = contexts[0]
                            pages = self._context.pages
                            if pages:
                                self._page = pages[0]
                            else:
                                self._page = await self._context.new_page()
                        else:
                            self._context = await self._browser.new_context()
                            self._page = await self._context.new_page()

                        self._cdp_url = f"http://localhost:{self.cdp_port}"
                        self._using_user_chrome = True
                        self._started = True
                        self._visible = True  # 已运行的 Chrome 肯定是可见的

                        logger.info(
                            f"[Browser] Connected to running Chrome (tabs: {len(self._context.pages)})"
                        )
                        return True

            except Exception as cdp_error:
                logger.debug(f"[Browser] No Chrome with debugging port: {cdp_error}")

            # 优先使用用户的 Chrome（保留登录状态）
            if self.use_user_chrome and self._chrome_path and self._chrome_user_data:
                # 尝试 1：直接使用用户数据目录
                try:
                    logger.info(f"[Browser] Launching user's Chrome: {self._chrome_path}")

                    self._context = await self._playwright.chromium.launch_persistent_context(
                        user_data_dir=self._chrome_user_data,
                        headless=headless,
                        executable_path=self._chrome_path,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            f"--remote-debugging-port={self.cdp_port}",
                        ],
                        channel="chrome",
                    )

                    self._browser = None
                    self._using_user_chrome = True

                    pages = self._context.pages
                    if pages:
                        self._page = pages[0]
                    else:
                        self._page = await self._context.new_page()

                    self._cdp_url = f"http://localhost:{self.cdp_port}"
                    self._started = True
                    self._visible = not headless
                    logger.info(
                        f"Browser MCP started with user's Chrome (visible={self._visible}, cdp={self._cdp_url})"
                    )
                    return True

                except Exception as chrome_error:
                    error_str = str(chrome_error)
                    logger.warning(f"[Browser] Failed to launch user's Chrome: {chrome_error}")

                    # 尝试 2：如果是目录被占用（exitCode=21），使用 OpenAkita 专用 profile
                    if "exitCode=21" in error_str or "already in use" in error_str.lower():
                        logger.info(
                            "[Browser] User data dir locked (Chrome is running). Using OpenAkita profile..."
                        )

                        try:
                            # 获取/创建 OpenAkita 专用 profile
                            openakita_profile = get_openakita_chrome_profile()

                            # 同步 cookies（如果用户 Chrome 没锁定关键文件）
                            sync_chrome_cookies(self._chrome_user_data, openakita_profile)

                            # 使用 OpenAkita profile 启动
                            self._context = (
                                await self._playwright.chromium.launch_persistent_context(
                                    user_data_dir=openakita_profile,
                                    headless=headless,
                                    executable_path=self._chrome_path,
                                    args=[
                                        "--disable-blink-features=AutomationControlled",
                                        "--no-sandbox",
                                        f"--remote-debugging-port={self.cdp_port}",
                                    ],
                                    channel="chrome",
                                )
                            )

                            self._browser = None
                            self._using_user_chrome = True  # 还是使用 Chrome，只是不同 profile

                            pages = self._context.pages
                            if pages:
                                self._page = pages[0]
                            else:
                                self._page = await self._context.new_page()

                            self._cdp_url = f"http://localhost:{self.cdp_port}"
                            self._started = True
                            self._visible = not headless
                            logger.info(
                                f"Browser MCP started with OpenAkita Chrome profile (visible={self._visible}, cdp={self._cdp_url})"
                            )
                            return True

                        except Exception as profile_error:
                            logger.warning(
                                f"[Browser] OpenAkita profile also failed: {profile_error}"
                            )

                    logger.info("[Browser] Falling back to Chromium...")

            # 回退：使用 Playwright 内置的 Chromium
            logger.info("[Browser] Launching Playwright Chromium")
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    f"--remote-debugging-port={self.cdp_port}",
                ],
            )

            # 保存 CDP URL 供 browser-use 使用
            self._cdp_url = f"http://localhost:{self.cdp_port}"
            self._using_user_chrome = False

            # 显式创建 context（支持多 tab）
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()

            # 设置默认超时
            self._page.set_default_timeout(30000)

            self._started = True
            self._visible = not headless
            logger.info(
                f"Browser MCP started with Chromium (visible={self._visible}, cdp={self._cdp_url})"
            )
            return True

        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("playwright")
            logger.error(f"Playwright 导入失败: {hint}")
            return False
        except Exception as e:
            logger.error(f"Failed to start browser: {e}")
            return False

    async def stop(self) -> None:
        """停止浏览器"""
        try:
            if self._using_user_chrome:
                # persistent context 模式：直接关闭 context
                if self._context:
                    await self._context.close()
            else:
                # 普通模式：按顺序关闭
                if self._page:
                    await self._page.close()
                if self._context:
                    await self._context.close()
                if self._browser:
                    await self._browser.close()

            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.warning(f"Error stopping browser: {e}")

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._started = False
        self._using_user_chrome = False
        logger.info("Browser MCP stopped")

    def get_tools(self) -> list[dict]:
        """获取工具定义列表"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.arguments,
            }
            for tool in self.TOOLS
        ]

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        调用工具

        Args:
            tool_name: 工具名称
            arguments: 参数

        Returns:
            {"success": bool, "result": Any, "error": str}
        """
        # browser_open 是特殊的，需要先处理（合并了 browser_status 功能）
        if tool_name == "browser_open":
            return await self._open_browser(
                visible=arguments.get("visible", True),
            )

        if not self._started:
            success = await self.start()
            if not success:
                return {
                    "success": False,
                    "error": "Browser not started. Please install playwright: pip install playwright && playwright install chromium",
                }

        try:
            if tool_name == "browser_navigate":
                return await self._navigate(arguments.get("url"))

            elif tool_name == "browser_click":
                return await self._click(arguments.get("selector"), arguments.get("text"))

            elif tool_name == "browser_type":
                return await self._type(
                    arguments.get("selector"), arguments.get("text"), arguments.get("clear", True)
                )

            elif tool_name == "browser_screenshot":
                return await self._screenshot(
                    arguments.get("full_page", False), arguments.get("path")
                )

            elif tool_name == "browser_get_content":
                return await self._get_content(
                    arguments.get("selector"), arguments.get("format", "text")
                )

            elif tool_name == "browser_wait":
                return await self._wait(arguments.get("selector"), arguments.get("timeout", 30000))

            elif tool_name == "browser_scroll":
                return await self._scroll(
                    arguments.get("direction", "down"), arguments.get("amount", 500)
                )

            elif tool_name == "browser_execute_js":
                return await self._execute_js(arguments.get("script"))

            elif tool_name == "browser_close":
                await self.stop()
                return {"success": True, "result": "Browser closed"}

            elif tool_name == "browser_status":
                # 向后兼容：内部调用重定向到 _get_status，对外已合并到 browser_open
                return await self._get_status()

            elif tool_name == "browser_list_tabs":
                return await self._list_tabs()

            elif tool_name == "browser_switch_tab":
                return await self._switch_tab(arguments.get("index", 0))

            elif tool_name == "browser_new_tab":
                return await self._new_tab(arguments.get("url"))

            elif tool_name == "browser_task":
                return await self._browser_task(
                    arguments.get("task"), arguments.get("max_steps", 15)
                )

            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}

        except Exception as e:
            error_str = str(e)
            logger.error(f"Browser tool error: {e}")

            # 检测是否是浏览器已关闭的错误
            if "closed" in error_str.lower() or "target" in error_str.lower():
                logger.warning(
                    "[Browser] Browser/page closed detected in call_tool, resetting state"
                )
                await self._reset_state()
                return {
                    "success": False,
                    "error": "浏览器连接已断开（可能被用户关闭）。\n"
                    "【重要】状态已重置，请直接调用 browser_open 重新启动浏览器，无需先调用 browser_close。",
                }

            return {"success": False, "error": error_str}

    # ==================== 工具实现 ====================

    async def _reset_state(self) -> None:
        """重置浏览器状态（不关闭资源，只清除引用）"""
        self._started = False
        self._browser = None
        self._context = None
        self._page = None
        self._using_user_chrome = False
        self._cdp_url = None
        logger.info("[Browser] State reset")

    async def _open_browser(self, visible: bool = True) -> dict:
        """
        启动浏览器或检查状态（合并了原 browser_status 功能）

        始终返回当前浏览器状态。如果已运行则直接返回状态，不重复启动。

        Args:
            visible: 是否显示浏览器窗口
        """
        # 如果已启动，验证连接是否有效并返回状态
        if self._started and self._context and self._page:
            try:
                current_url = self._page.url
                current_title = await self._page.title()
                all_pages = self._context.pages

                # 如果请求的可见模式与当前不同，需要重启
                if visible != self._visible:
                    logger.info(f"Browser mode change requested: visible={visible}, restarting...")
                    await self.stop()
                    # 继续到下面的启动逻辑
                else:
                    return {
                        "success": True,
                        "result": {
                            "is_open": True,
                            "status": "already_running",
                            "visible": self._visible,
                            "tab_count": len(all_pages),
                            "current_tab": {"url": current_url, "title": current_title},
                            "using_user_chrome": self._using_user_chrome,
                            "message": f"浏览器已在{'可见' if self._visible else '后台'}模式运行，共 {len(all_pages)} 个标签页",
                        },
                    }
            except Exception as e:
                # 浏览器已断开，重置状态
                logger.warning(f"[Browser] Browser connection lost: {e}, resetting state")
                await self._reset_state()
        elif self._started:
            # 状态不完整，重置
            logger.warning("[Browser] Incomplete browser state, resetting")
            await self._reset_state()

        # 启动浏览器
        success = await self.start(visible=visible)

        if success:
            # 启动成功，返回状态信息
            current_url = self._page.url if self._page else None
            current_title = None
            tab_count = 0
            try:
                if self._page:
                    current_title = await self._page.title()
                if self._context:
                    tab_count = len(self._context.pages)
            except Exception:
                pass

            result_data = {
                "is_open": True,
                "status": "started",
                "visible": self._visible,
                "tab_count": tab_count,
                "current_tab": {"url": current_url, "title": current_title},
                "using_user_chrome": self._using_user_chrome,
                "message": f"浏览器已启动 ({'可见模式' if self._visible else '后台模式'})",
            }

            # 检测外部浏览器后端可用性（非阻塞提示）
            try:
                devtools_info = detect_chrome_devtools_mcp()
                if devtools_info["available"] and not self._using_user_chrome:
                    result_data["hint"] = (
                        "提示：检测到 Chrome DevTools MCP 可用。如需保留登录状态，"
                        "可使用 call_mcp_tool('chrome-devtools', ...) 调用。"
                    )
            except Exception:
                pass

            return {"success": True, "result": result_data}
        else:
            # 启动失败，检查外部后端作为备选
            hints = []
            try:
                devtools_info = detect_chrome_devtools_mcp()
                if devtools_info["available"]:
                    hints.append(
                        "备选方案：Chrome DevTools MCP 可用，可通过 call_mcp_tool('chrome-devtools', 'navigate_page', {url: '...'}) 操作浏览器。"
                    )
                mcp_chrome_available = await check_mcp_chrome_extension()
                if mcp_chrome_available:
                    hints.append(
                        "备选方案：mcp-chrome 扩展已运行，可通过 call_mcp_tool('chrome-browser', ...) 操作浏览器。"
                    )
            except Exception:
                pass

            error_msg = "无法启动浏览器。请确保已安装 playwright: pip install playwright && playwright install chromium"
            if hints:
                error_msg += "\n\n" + "\n".join(hints)

            return {
                "success": False,
                "result": {"is_open": False, "status": "failed"},
                "error": error_msg,
            }

    async def _navigate(self, url: str) -> dict:
        """导航到 URL（增强版：等待页面完全加载）"""
        if not url:
            return {"success": False, "error": "URL is required"}

        # 自动添加协议
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            response = await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 等待页面网络空闲（最多 10 秒）
            try:
                await self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass  # 超时也继续

            # 额外等待让 JS 渲染完成
            await asyncio.sleep(1)

            title = await self._page.title()

            return {
                "success": True,
                "result": {
                    "url": self._page.url,
                    "title": title,
                    "status": response.status if response else None,
                    "message": f"已打开页面: {title}",
                },
            }
        except Exception as e:
            error_str = str(e)
            logger.error(f"Navigation failed: {e}")

            # 检测是否是浏览器已关闭的错误
            if "closed" in error_str.lower() or "target" in error_str.lower():
                logger.warning("[Browser] Browser/page closed, resetting state")
                await self._reset_state()
                return {
                    "success": False,
                    "error": "浏览器已关闭（可能被用户关闭或崩溃）。\n"
                    "【重要】请先调用 browser_close 清理状态，然后重新调用 browser_open 启动浏览器。",
                }

            return {
                "success": False,
                "error": f"页面加载失败: {error_str}\n"
                f"建议: 1) 检查 URL 是否正确 2) 该网站可能无法访问",
            }

    async def _click(self, selector: str | None, text: str | None) -> dict:
        """点击元素"""
        if text and not selector:
            # 通过文本查找
            selector = f"text={text}"

        if not selector:
            return {"success": False, "error": "selector or text is required"}

        await self._page.click(selector)

        return {"success": True, "result": f"Clicked: {selector}"}

    async def _type(self, selector: str, text: str, clear: bool) -> dict:
        """输入文本（带智能重试和遮挡处理）"""
        if not selector or not text:
            return {"success": False, "error": "selector and text are required"}

        # 常见搜索引擎的备用选择器映射
        SEARCH_BOX_ALTERNATIVES = {
            # 百度
            "#kw": ['input[name="wd"]', "input.s_ipt", "#kw"],
            'input[name="wd"]': ["#kw", "input.s_ipt"],
            # Google
            'input[name="q"]': ['textarea[name="q"]', "input.gLFyf", 'input[type="text"]'],
            "#q": ['input[name="q"]', 'textarea[name="q"]'],
            # Bing
            "#sb_form_q": ['input[name="q"]', "input.sb_form_q"],
        }

        # 智能重试策略
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                # 第一步：尝试等待元素可见
                try:
                    await self._page.wait_for_selector(selector, state="visible", timeout=5000)
                except Exception:
                    # 元素可能被遮挡，尝试处理
                    logger.info(
                        f"[BrowserType] Element {selector} not visible, trying to handle overlay..."
                    )
                    await self._handle_page_overlays()
                    # 再等一次
                    await self._page.wait_for_selector(selector, state="visible", timeout=5000)

                # 第二步：执行输入
                if clear:
                    await self._page.fill(selector, text)
                else:
                    await self._page.type(selector, text)

                return {"success": True, "result": f"Typed into {selector}: {text}"}

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Type attempt {attempt + 1} failed: {e}")

                if attempt < max_retries - 1:
                    # 尝试备用选择器
                    alt_selectors = SEARCH_BOX_ALTERNATIVES.get(selector, [])
                    for alt in alt_selectors:
                        if alt == selector:
                            continue
                        try:
                            logger.info(f"[BrowserType] Trying alternative selector: {alt}")
                            # 先处理可能的遮挡
                            await self._handle_page_overlays()
                            await self._page.wait_for_selector(alt, state="visible", timeout=3000)
                            if clear:
                                await self._page.fill(alt, text)
                            else:
                                await self._page.type(alt, text)
                            return {
                                "success": True,
                                "result": f"Typed into {alt} (alt selector): {text}",
                            }
                        except Exception:
                            continue

                    # 尝试：强制点击元素位置后输入（适用于被透明层遮挡的情况）
                    if attempt == max_retries - 2:
                        try:
                            logger.info("[BrowserType] Trying force click then type...")
                            element = self._page.locator(selector).first
                            # 滚动到元素
                            await element.scroll_into_view_if_needed()
                            # 强制点击
                            await element.click(force=True, timeout=3000)
                            # 输入文本
                            await element.fill(text) if clear else await element.type(text)
                            return {
                                "success": True,
                                "result": f"Typed into {selector} (force mode): {text}",
                            }
                        except Exception as force_error:
                            logger.warning(f"Force type also failed: {force_error}")

                    # 最后尝试：使用 JavaScript 直接设置值（终极方案）
                    if attempt == max_retries - 1:
                        try:
                            logger.info("[BrowserType] Trying JavaScript injection...")
                            js_result = await self._page.evaluate(
                                """(selector, text, clear) => {
                                const el = document.querySelector(selector);
                                if (!el) return { success: false, error: 'Element not found' };
                                if (clear) el.value = '';
                                el.value = text;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                                return { success: true };
                            }""",
                                selector,
                                text,
                                clear,
                            )
                            if js_result.get("success"):
                                return {
                                    "success": True,
                                    "result": f"Typed into {selector} (JavaScript mode): {text}",
                                }
                        except Exception as js_error:
                            logger.warning(f"JavaScript type also failed: {js_error}")

                    # 等待后重试
                    await asyncio.sleep(1)

        return {
            "success": False,
            "error": f"输入失败（重试 {max_retries} 次）: {last_error}\n"
            f"建议: 1) 先用 browser_screenshot 截图查看当前页面状态 "
            f"2) 使用 browser_click 点击页面空白处关闭可能的弹窗 "
            f"3) 使用 browser_get_content 获取页面内容确认元素选择器",
        }

    async def _handle_page_overlays(self):
        """处理常见的页面遮挡元素（弹窗、广告、登录提示等）"""
        current_url = self._page.url

        # 百度特殊处理：使用 JavaScript 移除遮罩层
        if "baidu.com" in current_url:
            try:
                await self._page.evaluate("""() => {
                    // 移除百度首页的各种遮罩层
                    const overlaySelectors = [
                        '.s-skin-container',  // 皮肤容器
                        '.s-isindex-wrap .c-tips-container',  // 提示容器
                        '.s-top-login-btn',  // 登录按钮
                        '#s-top-loginbtn',
                        '.soutu-env-nom498-index',  // 搜图容器
                        '#s_tab',  // 可能遮挡的 tab
                        '.s-p-top',  // 顶部区域
                    ];
                    overlaySelectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => {
                            el.style.display = 'none';
                        });
                    });

                    // 确保搜索框可见
                    const searchInput = document.querySelector('#kw') || document.querySelector('input[name="wd"]');
                    if (searchInput) {
                        searchInput.style.visibility = 'visible';
                        searchInput.style.display = 'block';
                        searchInput.style.opacity = '1';
                        // 确保父元素也可见
                        let parent = searchInput.parentElement;
                        while (parent && parent !== document.body) {
                            parent.style.visibility = 'visible';
                            parent.style.display = parent.style.display === 'none' ? 'block' : parent.style.display;
                            parent.style.opacity = '1';
                            parent = parent.parentElement;
                        }
                    }
                }""")
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug(f"[BrowserOverlay] Baidu overlay removal failed: {e}")

        # 常见的关闭按钮选择器
        close_selectors = [
            # 通用关闭按钮
            'button[aria-label="Close"]',
            'button[aria-label="关闭"]',
            ".close-btn",
            ".close-button",
            ".btn-close",
            '[class*="close"]',
            '[class*="dismiss"]',
            # 百度特定
            ".c-tips-container .close",
            ".login-guide-close",
            "#s-top-loginbtn",  # 不点登录，跳过
            # 各种弹窗
            ".modal-close",
            ".popup-close",
            'button:has-text("我知道了")',
            'button:has-text("关闭")',
            'button:has-text("跳过")',
            'button:has-text("Skip")',
        ]

        for selector in close_selectors:
            try:
                element = self._page.locator(selector).first
                if await element.is_visible():
                    await element.click(timeout=1000)
                    logger.info(f"[BrowserType] Closed overlay: {selector}")
                    await asyncio.sleep(0.3)  # 等待动画
            except Exception:
                continue

        # 按 Escape 键关闭可能的弹窗
        try:
            await self._page.keyboard.press("Escape")
            await asyncio.sleep(0.2)
        except Exception:
            pass

        # 点击页面空白处
        try:
            await self._page.mouse.click(10, 10)
            await asyncio.sleep(0.2)
        except Exception:
            pass

    async def _screenshot(self, full_page: bool, path: str | None) -> dict:
        """截图

        注意：为避免上下文爆炸，截图总是保存到文件，不返回 base64
        """
        # 检查是否在空白页
        current_url = self._page.url
        if current_url == "about:blank":
            return {
                "success": False,
                "error": "当前页面是空白页 (about:blank)，请先使用 browser_navigate 打开一个网页",
            }

        # 等待页面稳定（等待网络空闲或最多 3 秒）
        try:
            await self._page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass  # 超时也继续截图

        # 额外等待一点时间让页面渲染完成
        await asyncio.sleep(0.5)

        screenshot_bytes = await self._page.screenshot(full_page=full_page)

        # 获取当前页面信息
        page_title = await self._page.title()

        # 如果没有指定路径，自动生成（强制保存文件，不返回 base64 以避免上下文爆炸）
        if not path:
            from datetime import datetime

            screenshots_dir = Path("data/screenshots")
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = str(screenshots_dir / f"screenshot_{timestamp}.png")

        # 保存截图到文件
        Path(path).write_bytes(screenshot_bytes)
        return {
            "success": True,
            "result": {
                "saved_to": path,
                "page_url": current_url,
                "page_title": page_title,
                "message": f"截图已保存到: {path}",
                "hint": "如需将截图交付给用户，请使用 deliver_artifacts 工具，把此路径作为 artifacts[].path 传入",
            },
        }

    async def _get_content(self, selector: str | None, format: str) -> dict:
        """获取内容"""
        if selector:
            element = await self._page.query_selector(selector)
            if not element:
                return {"success": False, "error": f"Element not found: {selector}"}

            if format == "html":
                content = await element.inner_html()
            else:
                content = await element.inner_text()
        else:
            if format == "html":
                content = await self._page.content()
            else:
                content = await self._page.inner_text("body")

        return {"success": True, "result": content}

    async def _wait(self, selector: str | None, timeout: int) -> dict:
        """等待"""
        if selector:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return {"success": True, "result": f"Element appeared: {selector}"}
        else:
            await asyncio.sleep(timeout / 1000)
            return {"success": True, "result": f"Waited {timeout}ms"}

    async def _scroll(self, direction: str, amount: int) -> dict:
        """滚动"""
        if direction == "up":
            amount = -amount

        await self._page.evaluate(f"window.scrollBy(0, {amount})")

        return {"success": True, "result": f"Scrolled {direction} by {abs(amount)}px"}

    async def _execute_js(self, script: str) -> dict:
        """执行 JavaScript"""
        if not script:
            return {"success": False, "error": "script is required"}

        result = await self._page.evaluate(script)

        return {"success": True, "result": result}

    async def _get_status(self) -> dict:
        """获取浏览器状态"""
        # 注意：persistent context 模式下 _browser 是 None，但 _context 有值
        if not self._started or not self._context:
            return {
                "success": True,
                "result": {
                    "is_open": False,
                    "message": "浏览器未启动。使用 browser_open 启动浏览器。",
                },
            }

        try:
            # 验证 context 是否真的还活着
            all_pages = self._context.pages

            # 当前页面信息
            current_url = self._page.url if self._page else None
            current_title = await self._page.title() if self._page else None

            return {
                "success": True,
                "result": {
                    "is_open": True,
                    "visible": self._visible,
                    "tab_count": len(all_pages),
                    "current_tab": {"url": current_url, "title": current_title},
                    "message": f"浏览器{'可见' if self._visible else '后台'}运行中，共 {len(all_pages)} 个标签页",
                },
            }
        except Exception as e:
            # 浏览器连接已断开，重置状态
            logger.error(f"Failed to get browser status: {e}")
            error_str = str(e)
            if "closed" in error_str.lower() or "target" in error_str.lower():
                logger.warning("[Browser] Connection lost, resetting state")
                await self._reset_state()
                return {
                    "success": True,
                    "result": {
                        "is_open": False,
                        "message": "浏览器连接已断开（可能被用户关闭）。使用 browser_open 重新启动。",
                    },
                }
            return {
                "success": True,
                "result": {"is_open": True, "visible": self._visible, "error": str(e)},
            }

    async def _list_tabs(self) -> dict:
        """列出所有标签页"""
        # 注意：persistent context 模式下 _browser 是 None，但 _context 有值
        if not self._started or not self._context:
            return {"success": False, "error": "浏览器未启动"}

        try:
            # 使用显式管理的 context
            all_pages = self._context.pages if self._context else []

            tabs = []
            for i, page in enumerate(all_pages):
                try:
                    title = await page.title()
                    tabs.append(
                        {
                            "index": i,
                            "url": page.url,
                            "title": title,
                            "is_current": page == self._page,
                        }
                    )
                except Exception:
                    tabs.append(
                        {
                            "index": i,
                            "url": page.url,
                            "title": "(无法获取)",
                            "is_current": page == self._page,
                        }
                    )

            return {
                "success": True,
                "result": {"tabs": tabs, "count": len(tabs), "message": f"共 {len(tabs)} 个标签页"},
            }
        except Exception as e:
            logger.error(f"Failed to list tabs: {e}")
            return {"success": False, "error": f"获取标签页列表失败: {str(e)}"}

    async def _switch_tab(self, index: int) -> dict:
        """切换到指定标签页"""
        # 注意：persistent context 模式下 _browser 是 None，但 _context 有值
        if not self._started or not self._context:
            return {"success": False, "error": "浏览器未启动"}

        try:
            # 使用显式管理的 context
            all_pages = self._context.pages if self._context else []

            if index < 0 or index >= len(all_pages):
                return {
                    "success": False,
                    "error": f"标签页索引 {index} 无效。有效范围: 0-{len(all_pages) - 1}",
                }

            self._page = all_pages[index]
            await self._page.bring_to_front()

            title = await self._page.title()
            return {
                "success": True,
                "result": {
                    "switched_to": {"index": index, "url": self._page.url, "title": title},
                    "message": f"已切换到标签页 {index}: {title}",
                },
            }
        except Exception as e:
            logger.error(f"Failed to switch tab: {e}")
            return {"success": False, "error": f"切换标签页失败: {str(e)}"}

    async def _new_tab(self, url: str) -> dict:
        """在新标签页打开 URL"""
        # 注意：persistent context 模式下 _browser 是 None，但 _context 有值
        if not self._started or not self._context:
            success = await self.start()
            if not success:
                return {"success": False, "error": "浏览器启动失败"}

        try:
            # 使用显式管理的 context
            if not self._context:
                self._context = await self._browser.new_context()

            # 检查是否可以复用空白页
            reused_blank = False
            if self._page and self._page.url in ("about:blank", ""):
                # 复用空白页，直接导航
                new_page = self._page
                reused_blank = True
            else:
                # 创建新页面
                new_page = await self._context.new_page()

            await new_page.goto(url, wait_until="domcontentloaded")

            # 切换到新页面
            self._page = new_page

            title = await new_page.title()

            # 统计当前 tab 数量
            all_pages = self._context.pages

            return {
                "success": True,
                "result": {
                    "url": url,
                    "title": title,
                    "tab_index": len(all_pages) - 1,
                    "total_tabs": len(all_pages),
                    "reused_blank": reused_blank,
                    "message": f"已在{'空白' if reused_blank else '新'}标签页打开: {title}",
                },
            }
        except Exception as e:
            error_str = str(e)
            logger.error(f"Failed to open new tab: {e}")

            # 检测是否是浏览器已关闭的错误
            if "closed" in error_str.lower() or "target" in error_str.lower():
                logger.warning("[Browser] Browser/page closed, resetting state")
                await self._reset_state()
                return {
                    "success": False,
                    "error": "浏览器已关闭。请先调用 browser_close 然后重新调用 browser_open 启动浏览器。",
                }

            return {"success": False, "error": f"打开新标签页失败: {error_str}"}

    async def _browser_task(self, task: str, max_steps: int = 15) -> dict:
        """
        使用 browser-use Agent 自主完成浏览器任务

        这是推荐的高层接口，适用于多步骤操作。
        browser-use Agent 会自动规划和执行所有步骤。

        特性：
        - 通过 CDP 复用 OpenAkita 已启动的浏览器
        - 继承 OpenAkita 系统配置的 LLM

        Args:
            task: 任务描述，例如 "打开百度搜索福建福州并截图"
            max_steps: 最大执行步骤数

        Returns:
            {"success": bool, "result": str, "error": str}
        """
        if not task:
            return {"success": False, "error": "task is required"}

        try:
            # 延迟导入 browser-use
            from browser_use import Agent as BUAgent
            from browser_use import Browser as BUBrowser

            # 确保浏览器已启动（带 CDP 端口）
            if not self._started:
                success = await self.start(visible=True)
                if not success:
                    return {"success": False, "error": "浏览器启动失败"}

            logger.info(f"[BrowserTask] Starting task: {task}")

            # 方式1：通过 CDP 连接到现有浏览器（复用 OpenAkita 的浏览器）
            bu_browser = None
            if self._cdp_url:
                try:
                    # browser-use 0.11.x API: 使用 cdp_url 连接时设置 is_local=True
                    bu_browser = BUBrowser(cdp_url=self._cdp_url, is_local=True)
                    logger.info(f"[BrowserTask] Connected via CDP: {self._cdp_url}")
                except Exception as cdp_error:
                    logger.warning(
                        f"[BrowserTask] CDP connection failed: {cdp_error}, falling back to new browser"
                    )

            # 方式2：如果 CDP 连接失败，创建新浏览器
            if bu_browser is None:
                # browser-use 0.11.x API: headless 参数
                bu_browser = BUBrowser(headless=not self._visible, is_local=True)
                logger.info("[BrowserTask] Created new browser instance")

            # 获取 LLM 配置
            # 优先级：1. 注入的配置 2. 环境变量 3. ChatBrowserUse
            llm = None
            import os

            class _BrowserUseLLMProxy:
                """
                browser-use 会直接访问 llm.provider / llm.model。
                但 langchain_openai.ChatOpenAI 往往不允许动态挂载新属性（pydantic/slots），
                因此用一个轻量代理对象显式提供这两个字段，其余属性/方法全部转发。
                """

                def __init__(self, inner, *, provider: str, model: str):
                    self._inner = inner
                    self.provider = provider
                    self.model = model

                def __getattr__(self, name: str):
                    return getattr(self._inner, name)

            def _ensure_browser_use_llm_contract(llm_obj, *, provider: str, model: str):
                """
                返回满足 browser-use 契约的 llm 对象（必要时会用代理包装）。
                """
                # 如果已经满足则直接返回
                if hasattr(llm_obj, "provider") and hasattr(llm_obj, "model"):
                    return llm_obj

                # 先尝试直接写入（如果对象允许）
                try:
                    if not hasattr(llm_obj, "provider"):
                        llm_obj.provider = provider
                    if not hasattr(llm_obj, "model"):
                        llm_obj.model = model
                    if hasattr(llm_obj, "provider") and hasattr(llm_obj, "model"):
                        return llm_obj
                except Exception:
                    pass

                # 退化为代理包装（稳定且不依赖 inner 是否允许 set attribute）
                return _BrowserUseLLMProxy(llm_obj, provider=provider, model=model)

            # 1. 使用注入的 LLM 配置（从 Agent 继承）
            if self._llm_config:
                from langchain_openai import ChatOpenAI

                model = self._llm_config.get("model", "")
                api_key = self._llm_config.get("api_key")
                base_url = self._llm_config.get("base_url")

                if api_key:
                    llm = ChatOpenAI(
                        model=model,
                        api_key=api_key,
                        base_url=base_url,
                    )
                    llm = _ensure_browser_use_llm_contract(llm, provider="openai", model=model)
                    logger.info(f"[BrowserTask] Using inherited LLM config: {model}")

            # 2. 从环境变量获取
            if llm is None:
                from langchain_openai import ChatOpenAI

                api_key = os.getenv("OPENAI_API_KEY")
                base_url = os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")
                model = os.getenv("OPENAI_MODEL", "")

                if api_key:
                    llm = ChatOpenAI(
                        model=model,
                        api_key=api_key,
                        base_url=base_url,
                    )
                    llm = _ensure_browser_use_llm_contract(llm, provider="openai", model=model)
                    logger.info(f"[BrowserTask] Using env LLM: {model}")

            # 3. 尝试 ChatBrowserUse（需要 BROWSER_USE_API_KEY）
            if llm is None:
                try:
                    from browser_use import ChatBrowserUse

                    llm = ChatBrowserUse()
                    logger.info("[BrowserTask] Using ChatBrowserUse")
                except Exception:
                    pass

            if llm is None:
                return {
                    "success": False,
                    "error": "No LLM configured. Please call browser_mcp.set_llm_config() or set OPENAI_API_KEY environment variable.",
                }

            # 创建 browser-use Agent
            agent = BUAgent(
                task=task,
                llm=llm,
                browser=bu_browser,
                max_steps=max_steps,
            )

            # 执行任务
            history = await agent.run()

            # 获取结果
            final_result = (
                history.final_result() if hasattr(history, "final_result") else str(history)
            )

            # 注意：如果使用 CDP 连接，不关闭浏览器（由 OpenAkita 管理）
            # 只有新建的浏览器才关闭
            if not self._cdp_url:
                await bu_browser.close()

            logger.info(f"[BrowserTask] Task completed: {task}")

            return {
                "success": True,
                "result": {
                    "task": task,
                    "steps_taken": len(history.history) if hasattr(history, "history") else 0,
                    "final_result": final_result,
                    "message": f"任务完成: {task}",
                },
            }

        except ImportError as e:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("browser_use") or import_or_hint("langchain_openai") or str(e)
            logger.error(f"[BrowserTask] Import error: {hint}")
            return {
                "success": False,
                "error": hint,
            }
        except Exception as e:
            logger.error(f"[BrowserTask] Error: {e}")
            return {"success": False, "error": f"任务执行失败: {str(e)}"}

    def set_llm_config(self, config: dict) -> None:
        """
        设置 LLM 配置（由 Agent 注入）

        Args:
            config: {
                "model": str,        # 模型名称
                "api_key": str,      # API Key
                "base_url": str,     # API Base URL (可选)
            }
        """
        self._llm_config = config
        logger.info(f"[BrowserMCP] LLM config set: model={config.get('model')}")

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def current_url(self) -> str | None:
        return self._page.url if self._page else None

    @property
    def cdp_url(self) -> str | None:
        """获取 CDP 连接地址"""
        return self._cdp_url


# 单例
_browser_mcp: BrowserMCP | None = None


def get_browser_mcp(headless: bool = False) -> BrowserMCP:
    """获取 BrowserMCP 单例"""
    global _browser_mcp
    if _browser_mcp is None:
        _browser_mcp = BrowserMCP(headless=headless)
    return _browser_mcp
