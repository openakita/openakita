"""
BrowserManager - 浏览器生命周期管理

通过状态机管理 Playwright 浏览器的启动、停止和健康检查。
对外提供 ``page``（供 PlaywrightTools）和 ``cdp_url``（供 BrowserUseRunner）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LAUNCH_TIMEOUT = 30  # seconds

_COMMON_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-first-run",
    "--disable-features=VizDisplayCompositor",
]

_SERVER_EXTRA_ARGS = [
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
]


def _is_server_environment() -> bool:
    """检测是否运行在无 GUI 的服务器环境（如 Windows Server / headless Linux）。

    注意：PyInstaller 打包的桌面应用（IS_FROZEN=True）不等于服务器，
    不应因为是打包环境就强制 headless / --disable-gpu。
    """
    if platform.system() != "Windows":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return True
        return False

    # Windows: 远程桌面会话
    try:
        import ctypes
        SM_REMOTESESSION = 0x1000
        if ctypes.windll.user32.GetSystemMetrics(SM_REMOTESESSION) != 0:
            return True
    except Exception:
        pass

    # Windows: 通过注册表检测 Windows Server 版本
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        )
        product_name, _ = winreg.QueryValueEx(key, "ProductName")
        winreg.CloseKey(key)
        if "server" in product_name.lower():
            return True
    except Exception:
        pass

    return False


def _find_bundled_browser_executable() -> str | None:
    """在 PyInstaller 打包目录中搜索内置的 Chromium/Chrome 可执行文件。

    搜索路径（按优先级）：
    1. {base}/browser/chrome.exe                          — 直接打包位置
    2. {base}/playwright-browsers/chromium-*/chrome-win/   — Playwright 标准结构
    3. {base}/playwright-browsers/chromium-*/chrome-linux/
    """
    import sys

    from openakita.runtime_env import IS_FROZEN
    if not IS_FROZEN:
        return None

    search_roots: list[Path] = []

    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        search_roots.append(Path(_meipass))

    exe_dir = Path(sys.executable).parent
    internal_dir = exe_dir / "_internal"
    if internal_dir.is_dir() and internal_dir not in search_roots:
        search_roots.append(internal_dir)

    is_win = platform.system() == "Windows"
    exe_name = "chrome.exe" if is_win else "chrome"
    headless_name = "headless_shell.exe" if is_win else "headless_shell"

    candidates: list[Path] = []
    for root in search_roots:
        candidates.append(root / "browser" / exe_name)

        pw_dir = root / "playwright-browsers"
        if pw_dir.is_dir():
            for chromium_dir in sorted(pw_dir.glob("chromium-*"), reverse=True):
                if is_win:
                    candidates.append(chromium_dir / "chrome-win" / exe_name)
                else:
                    candidates.append(chromium_dir / "chrome-linux" / exe_name)
                    candidates.append(chromium_dir / "chrome-linux" / headless_name)

        candidates.append(root / "browser" / headless_name)

    for path in candidates:
        if path.is_file():
            logger.info(f"[Browser] Found bundled browser executable: {path}")
            return str(path)

    searched = list({str(c.parent) for c in candidates[:6]})
    logger.debug(f"[Browser] No bundled browser found in: {searched}")
    return None


class BrowserState(Enum):
    IDLE = "idle"
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"
    STOPPING = "stopping"


class StartupStrategy(Enum):
    CDP_CONNECT = "cdp_connect"
    USER_CHROME_USER_PROFILE = "user_chrome_user_profile"
    USER_CHROME_OA_PROFILE = "user_chrome_oa_profile"
    BUNDLED_CHROMIUM = "bundled_chromium"


class BrowserManager:
    """浏览器生命周期管理（状态机 + 多策略启动 + 回退链）"""

    def __init__(self, cdp_port: int = 9222, use_user_chrome: bool = True):
        self._cdp_port = cdp_port
        self._use_user_chrome = use_user_chrome

        # Playwright 资源
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None

        # 公共状态
        self.state = BrowserState.IDLE
        self.visible: bool = True
        self.using_user_chrome: bool = False

        self._cdp_url: str | None = None
        self._last_successful_strategy: StartupStrategy | None = None
        self._startup_errors: list[str] = []
        self._startup_lock = asyncio.Lock()
        self._is_server = _is_server_environment()

        # Chrome 检测
        from .chrome_finder import detect_chrome_installation
        self._chrome_path, self._chrome_user_data = detect_chrome_installation()

        # 内置 Chromium 检测（PyInstaller 打包环境）
        self._bundled_executable = _find_bundled_browser_executable()

        if self._is_server:
            logger.info("[Browser] Server environment detected, will use extra launch args")

    # ── 公共属性 ────────────────────────────────────────

    @property
    def page(self) -> Any | None:
        return self._page

    @property
    def context(self) -> Any | None:
        return self._context

    @property
    def cdp_url(self) -> str | None:
        return self._cdp_url

    @property
    def is_ready(self) -> bool:
        return self.state == BrowserState.READY

    @property
    def current_url(self) -> str | None:
        return self._page.url if self._page else None

    # ── 启动 / 停止 ────────────────────────────────────

    async def start(self, visible: bool = True) -> bool:
        async with self._startup_lock:
            if self.state == BrowserState.READY:
                if visible != self.visible:
                    logger.info(f"Browser mode change requested: visible={visible}, restarting...")
                    await self._stop_internal()
                else:
                    return True

            self.state = BrowserState.STARTING
            self.visible = visible
            self._startup_errors.clear()

            headless = not visible

            self._setup_browsers_path()

            if not await self._start_playwright_driver():
                return False

            strategies = self._build_strategy_order()

            for strategy in strategies:
                try:
                    ok = await self._try_strategy(strategy, headless)
                    if ok:
                        self.state = BrowserState.READY
                        self._last_successful_strategy = strategy
                        logger.info(
                            f"Browser started via {strategy.value} "
                            f"(visible={self.visible}, cdp={self._cdp_url})"
                        )
                        return True
                except Exception as e:
                    msg = f"{strategy.value}: {e}"
                    self._startup_errors.append(msg)
                    logger.warning(
                        f"[Browser] Strategy {strategy.value} failed: {e}",
                        exc_info=True,
                    )

            if not headless:
                logger.info("[Browser] All headed strategies failed, retrying in headless mode...")
                for strategy in strategies:
                    try:
                        ok = await self._try_strategy(strategy, headless=True)
                        if ok:
                            self.state = BrowserState.READY
                            self._last_successful_strategy = strategy
                            self.visible = False
                            logger.info(
                                f"Browser started via {strategy.value} "
                                f"(headless fallback, cdp={self._cdp_url})"
                            )
                            return True
                    except Exception as e:
                        logger.debug(f"[Browser] Headless fallback {strategy.value} also failed: {e}")

            logger.error(
                f"[Browser] All strategies failed: {'; '.join(self._startup_errors)}"
            )
            self.state = BrowserState.ERROR
            await self._cleanup_playwright()
            return False

    async def _start_playwright_driver(self) -> bool:
        """启动 Playwright driver 进程（最多重试 2 次），失败时返回 False。"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("playwright")
            logger.error(f"Playwright 导入失败: {hint}")
            self.state = BrowserState.ERROR
            return False

        max_attempts = 2
        last_err = ""

        for attempt in range(1, max_attempts + 1):
            try:
                pw_ctx = async_playwright()
                self._playwright = await asyncio.wait_for(
                    pw_ctx.start(), timeout=20,
                )
                return True
            except asyncio.TimeoutError:
                last_err = f"Playwright driver 启动超时 (20s, attempt {attempt}/{max_attempts})"
                logger.warning(f"[Browser] {last_err}")
                await self._cleanup_playwright()
                if attempt < max_attempts:
                    await asyncio.sleep(1)
            except Exception as e:
                last_err = f"Playwright driver 启动失败: {type(e).__name__}: {e}"
                logger.warning(f"[Browser] {last_err}", exc_info=True)
                await self._cleanup_playwright()
                if attempt < max_attempts:
                    await asyncio.sleep(1)

        self._startup_errors.append(last_err)
        logger.error(f"[Browser] {last_err}")
        self.state = BrowserState.ERROR
        return False

    async def stop(self) -> None:
        async with self._startup_lock:
            await self._stop_internal()

    async def ensure_ready(self) -> bool:
        """如果浏览器未就绪则自动启动，就绪则做健康检查。"""
        if self.state == BrowserState.READY:
            if await self._health_check():
                return True
            logger.warning("[Browser] Health check failed, restarting...")
            await self.stop()

        return await self.start(visible=self.visible)

    async def get_status(self) -> dict:
        """返回完整状态信息，供 browser_open / browser_status 使用。"""
        if self.state != BrowserState.READY or not self._context:
            return {
                "is_open": False,
                "state": self.state.value,
                "errors": list(self._startup_errors),
            }

        try:
            all_pages = self._context.pages
            current_url = self._page.url if self._page else None
            current_title = await self._page.title() if self._page else None
            return {
                "is_open": True,
                "state": self.state.value,
                "visible": self.visible,
                "tab_count": len(all_pages),
                "current_tab": {"url": current_url, "title": current_title},
                "using_user_chrome": self.using_user_chrome,
            }
        except Exception as e:
            logger.error(f"Failed to get browser status: {e}")
            return {"is_open": True, "state": self.state.value, "error": str(e)}

    async def reset_state(self) -> None:
        """只清除引用不关闭资源（用于检测到浏览器被外部关闭时）。"""
        self.state = BrowserState.IDLE
        self._browser = None
        self._context = None
        self._page = None
        self.using_user_chrome = False
        self._cdp_url = None
        logger.info("[Browser] State reset")

    # ── 内部 ────────────────────────────────────────────

    def _setup_browsers_path(self) -> None:
        """设置 PLAYWRIGHT_BROWSERS_PATH 环境变量。

        如果发现了内置的 chrome.exe（_bundled_executable），则不需要设置此变量，
        因为会通过 executable_path 参数直接指定。
        """
        if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
            return

        # 如果有内置可执行文件，跳过 — 会在 _try_bundled_chromium 里用 executable_path
        if self._bundled_executable:
            logger.info(
                f"[Browser] Will use bundled executable directly: "
                f"{self._bundled_executable}"
            )
            return

        from openakita.runtime_env import IS_FROZEN

        if IS_FROZEN:
            import sys
            _meipass = getattr(sys, "_MEIPASS", None)
            if _meipass:
                bundled = Path(_meipass) / "playwright-browsers"
                if bundled.is_dir():
                    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
                    logger.info(f"[Browser] Using bundled playwright-browsers: {bundled}")
                    return

        browsers_dir = Path.home() / ".openakita" / "modules" / "browser" / "browsers"
        if browsers_dir.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
            logger.info(f"[Browser] Using external Chromium: {browsers_dir}")

    def _build_strategy_order(self) -> list[StartupStrategy]:
        """根据历史成功策略决定尝试顺序。"""
        full_order = [
            StartupStrategy.CDP_CONNECT,
            StartupStrategy.USER_CHROME_USER_PROFILE,
            StartupStrategy.USER_CHROME_OA_PROFILE,
            StartupStrategy.BUNDLED_CHROMIUM,
        ]

        if self._last_successful_strategy:
            order = [self._last_successful_strategy]
            for s in full_order:
                if s != self._last_successful_strategy:
                    order.append(s)
            return order

        if not (self._use_user_chrome and self._chrome_path and self._chrome_user_data):
            return [StartupStrategy.CDP_CONNECT, StartupStrategy.BUNDLED_CHROMIUM]

        return full_order

    async def _try_strategy(self, strategy: StartupStrategy, headless: bool) -> bool:
        if strategy == StartupStrategy.CDP_CONNECT:
            return await self._try_cdp_connect()
        elif strategy == StartupStrategy.USER_CHROME_USER_PROFILE:
            return await self._try_user_chrome(headless, use_oa_profile=False)
        elif strategy == StartupStrategy.USER_CHROME_OA_PROFILE:
            return await self._try_user_chrome(headless, use_oa_profile=True)
        elif strategy == StartupStrategy.BUNDLED_CHROMIUM:
            return await self._try_bundled_chromium(headless)
        return False

    async def _try_cdp_connect(self) -> bool:
        """尝试连接已运行的 Chrome 调试端口。"""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://localhost:{self._cdp_port}/json/version", timeout=2.0,
            )
            if response.status_code != 200:
                return False

        logger.info(f"[Browser] Found Chrome at localhost:{self._cdp_port}")

        self._browser = await asyncio.wait_for(
            self._playwright.chromium.connect_over_cdp(
                f"http://localhost:{self._cdp_port}"
            ),
            timeout=15,
        )

        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
        else:
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()

        self._cdp_url = f"http://localhost:{self._cdp_port}"
        self.using_user_chrome = True
        self.visible = True
        logger.info(f"[Browser] Connected to running Chrome (tabs: {len(self._context.pages)})")
        return True

    def _build_launch_args(self) -> list[str]:
        """构建 Chromium 启动参数列表。"""
        args = list(_COMMON_CHROMIUM_ARGS)
        args.append(f"--remote-debugging-port={self._cdp_port}")
        if self._is_server:
            args.extend(_SERVER_EXTRA_ARGS)
        return args

    async def _try_user_chrome(self, headless: bool, *, use_oa_profile: bool) -> bool:
        """使用用户 Chrome 启动 persistent context。"""
        if not self._chrome_path:
            raise RuntimeError("Chrome executable not found")

        if use_oa_profile:
            from .chrome_finder import get_openakita_chrome_profile, sync_chrome_cookies
            user_data = get_openakita_chrome_profile()
            if self._chrome_user_data:
                sync_chrome_cookies(self._chrome_user_data, user_data)
            label = "OpenAkita profile"
        else:
            if not self._chrome_user_data:
                raise RuntimeError("Chrome user data dir not found")
            user_data = self._chrome_user_data
            label = "user profile"

        logger.info(f"[Browser] Launching Chrome with {label}: {self._chrome_path}")

        self._context = await asyncio.wait_for(
            self._playwright.chromium.launch_persistent_context(
                user_data_dir=user_data,
                headless=headless,
                executable_path=self._chrome_path,
                args=self._build_launch_args(),
                channel="chrome",
                timeout=_LAUNCH_TIMEOUT * 1000,
            ),
            timeout=_LAUNCH_TIMEOUT + 5,
        )

        self._browser = None
        self.using_user_chrome = True

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()

        self._cdp_url = f"http://localhost:{self._cdp_port}"
        self.visible = not headless
        logger.info(f"Browser started with Chrome ({label}, visible={self.visible})")
        return True

    def _preflight_chromium(self) -> str | None:
        """预检 Chromium 二进制是否可用，返回错误信息或 None。"""
        try:
            exe = self._playwright.chromium.executable_path
        except Exception:
            return None

        if not exe:
            return None

        exe_path = Path(exe)
        if not exe_path.exists():
            hint = (
                f"Chromium 可执行文件不存在: {exe}\n"
                "请运行: playwright install chromium"
            )
            browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "(default)")
            logger.error(
                f"[Browser] Chromium preflight FAIL: {hint} "
                f"(PLAYWRIGHT_BROWSERS_PATH={browsers_path})"
            )
            return hint

        if platform.system() != "Windows":
            if not os.access(exe, os.X_OK):
                return f"Chromium 可执行文件无执行权限: {exe}"

        file_size = exe_path.stat().st_size
        if file_size < 1_000_000:
            hint = f"Chromium 二进制文件异常（仅 {file_size} bytes），可能下载不完整: {exe}"
            logger.error(f"[Browser] {hint}")
            return hint

        logger.info(f"[Browser] Chromium binary verified: {exe} ({file_size / 1024 / 1024:.1f} MB)")
        return None

    async def _try_bundled_chromium(self, headless: bool) -> bool:
        """使用 Chromium 启动。

        策略（按顺序）：
        1. persistent_context — 原子式创建浏览器 + 上下文 + 页面，避免进程间隙崩溃
        2. launch + new_context + new_page — 传统方式兜底
        """
        exe_path = self._bundled_executable

        if exe_path:
            logger.info(f"[Browser] Using bundled executable: {exe_path}")
        else:
            preflight_err = self._preflight_chromium()
            if preflight_err:
                raise RuntimeError(preflight_err)

        effective_headless = headless
        if self._is_server and not headless:
            logger.info("[Browser] Server environment: forcing headless mode for Chromium")
            effective_headless = True

        exe_label = "bundled" if exe_path else "playwright"
        logger.info(
            f"[Browser] Launching Chromium "
            f"(headless={effective_headless}, exe={exe_label})"
        )

        last_err: Exception | None = None

        # --- 策略 1: persistent_context（原子启动，避免 new_page 崩溃）---
        try:
            ok = await self._launch_persistent(exe_path, effective_headless)
            if ok:
                return True
        except Exception as e:
            last_err = e
            logger.info(
                f"[Browser] persistent_context failed ({e}), "
                "trying standard launch..."
            )
            await self._close_browser_silently()

        # --- 策略 2: 传统 launch + new_context + new_page ---
        try:
            ok = await self._launch_standard(exe_path, effective_headless)
            if ok:
                return True
        except Exception as e:
            last_err = e
            logger.debug(f"[Browser] standard launch also failed: {e}")
            await self._close_browser_silently()

        raise last_err or RuntimeError("Chromium launch failed")

    async def _launch_persistent(
        self, exe_path: str | None, headless: bool,
    ) -> bool:
        """用 launch_persistent_context 原子启动浏览器 + 页面。"""
        import tempfile
        user_data = tempfile.mkdtemp(prefix="oa_chromium_")

        kwargs: dict[str, Any] = {
            "user_data_dir": user_data,
            "headless": headless,
            "args": self._build_launch_args(),
            "timeout": _LAUNCH_TIMEOUT * 1000,
        }
        if exe_path:
            kwargs["executable_path"] = exe_path

        self._context = await asyncio.wait_for(
            self._playwright.chromium.launch_persistent_context(**kwargs),
            timeout=_LAUNCH_TIMEOUT + 5,
        )
        self._browser = None
        self.using_user_chrome = False
        self._cdp_url = f"http://localhost:{self._cdp_port}"

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        self._page.set_default_timeout(30000)

        self.visible = not headless
        logger.info(
            f"Browser started with Chromium persistent_context "
            f"(visible={self.visible})"
        )
        return True

    async def _launch_standard(
        self, exe_path: str | None, headless: bool,
    ) -> bool:
        """传统 launch + new_context + new_page。"""
        launch_kwargs: dict[str, Any] = {
            "headless": headless,
            "args": self._build_launch_args(),
            "timeout": _LAUNCH_TIMEOUT * 1000,
        }
        if exe_path:
            launch_kwargs["executable_path"] = exe_path

        self._browser = await asyncio.wait_for(
            self._playwright.chromium.launch(**launch_kwargs),
            timeout=_LAUNCH_TIMEOUT + 5,
        )

        if not self._browser.is_connected():
            raise RuntimeError("Browser process exited immediately after launch")

        self._cdp_url = f"http://localhost:{self._cdp_port}"
        self.using_user_chrome = False

        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        self._page.set_default_timeout(30000)

        self.visible = not headless
        logger.info(f"Browser started with Chromium standard launch (visible={self.visible})")
        return True

    async def _close_browser_silently(self) -> None:
        """关闭浏览器资源但不清理 Playwright driver。"""
        for resource in (self._page, self._context, self._browser):
            if resource:
                try:
                    await resource.close()
                except Exception:
                    pass
        self._page = None
        self._context = None
        self._browser = None

    async def _health_check(self) -> bool:
        """快速检查浏览器连接是否存活。"""
        try:
            if not self._page or not self._context:
                return False
            _ = self._page.url
            _ = self._context.pages
            return True
        except Exception:
            return False

    async def _stop_internal(self) -> None:
        """实际停止流程（不加锁，由调用方保证锁）。"""
        prev = self.state
        self.state = BrowserState.STOPPING
        try:
            if self.using_user_chrome:
                if self._context:
                    await self._context.close()
            else:
                if self._page:
                    await self._page.close()
                if self._context:
                    await self._context.close()
                if self._browser:
                    await self._browser.close()
        except Exception as e:
            logger.warning(f"Error stopping browser: {e}")

        await self._cleanup_playwright()
        self._page = None
        self._context = None
        self._browser = None
        self.using_user_chrome = False
        self._cdp_url = None
        self.state = BrowserState.IDLE
        if prev == BrowserState.READY:
            logger.info("Browser stopped")

    async def _cleanup_playwright(self) -> None:
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
