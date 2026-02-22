"""
BrowserManager - 浏览器生命周期管理

通过状态机管理 Playwright 浏览器的启动、停止和健康检查。
对外提供 ``page``（供 PlaywrightTools）和 ``cdp_url``（供 BrowserUseRunner）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LAUNCH_TIMEOUT = 30  # seconds


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

        # Chrome 检测
        from .chrome_finder import detect_chrome_installation
        self._chrome_path, self._chrome_user_data = detect_chrome_installation()

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

            try:
                from playwright.async_api import async_playwright

                self._playwright = await asyncio.wait_for(
                    async_playwright().start(), timeout=15,
                )
            except ImportError:
                from openakita.tools._import_helper import import_or_hint
                hint = import_or_hint("playwright")
                logger.error(f"Playwright 导入失败: {hint}")
                self.state = BrowserState.ERROR
                return False
            except Exception as e:
                self._startup_errors.append(f"Playwright start failed: {e}")
                logger.error(f"Failed to start Playwright: {e}")
                self.state = BrowserState.ERROR
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
                    logger.warning(f"[Browser] Strategy {strategy.value} failed: {e}")

            logger.error(
                f"[Browser] All strategies failed: {'; '.join(self._startup_errors)}"
            )
            self.state = BrowserState.ERROR
            await self._cleanup_playwright()
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
        """设置 PLAYWRIGHT_BROWSERS_PATH 环境变量。"""
        if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
            return

        from openakita.runtime_env import IS_FROZEN

        if IS_FROZEN:
            import sys
            _meipass = getattr(sys, "_MEIPASS", None)
            if _meipass:
                bundled = Path(_meipass) / "playwright-browsers"
                if bundled.is_dir():
                    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
                    logger.info(f"[Browser] Using bundled Chromium: {bundled}")
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
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    f"--remote-debugging-port={self._cdp_port}",
                ],
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

    async def _try_bundled_chromium(self, headless: bool) -> bool:
        """使用 Playwright 内置 Chromium 启动。"""
        logger.info("[Browser] Launching Playwright Chromium")

        self._browser = await asyncio.wait_for(
            self._playwright.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    f"--remote-debugging-port={self._cdp_port}",
                ],
                timeout=_LAUNCH_TIMEOUT * 1000,
            ),
            timeout=_LAUNCH_TIMEOUT + 5,
        )

        self._cdp_url = f"http://localhost:{self._cdp_port}"
        self.using_user_chrome = False

        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        self._page.set_default_timeout(30000)

        self.visible = not headless
        logger.info(f"Browser started with Chromium (visible={self.visible})")
        return True

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
