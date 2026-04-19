"""
BrowserManager - browser lifecycle management

Manages Playwright browser startup, shutdown, and health checks via a state machine.
Exposes ``page`` (for PlaywrightTools) and ``cdp_url`` (for external CDP integration).
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_IS_MAC = platform.system() == "Darwin"

# Mach-O / fat binary magic bytes (covers both byte orders)
_MACHO_MAGICS = frozenset(
    {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",  # 32-bit
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",  # 64-bit
        b"\xca\xfe\xba\xbe",  # universal (fat)
    }
)

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
    """Detect whether we're running in a GUI-less server environment (e.g. Windows Server / headless Linux).

    Note: a PyInstaller-packaged desktop app (IS_FROZEN=True) is not the same as a server;
    don't force headless / --disable-gpu just because it's a packaged environment.
    """
    system = platform.system()

    # macOS uses Quartz/Aqua and does not rely on DISPLAY/WAYLAND_DISPLAY
    if system == "Darwin":
        return False

    if system != "Windows":
        # Linux: check X11/Wayland display server
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return True
        return False

    # Windows: remote desktop session
    try:
        import ctypes

        SM_REMOTESESSION = 0x1000
        if ctypes.windll.user32.GetSystemMetrics(SM_REMOTESESSION) != 0:
            return True
    except Exception:
        pass

    # Windows: detect Windows Server via registry
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


def _is_native_executable(path: Path) -> bool:
    """Check the file header to determine if it's a Mach-O binary or a #! script."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        return head in _MACHO_MAGICS or head[:2] == b"#!"
    except OSError:
        return False


def _ensure_macos_executability(target: Path) -> None:
    """Ensure all native binaries under the directory are executable and have no quarantine attribute.

    PyInstaller ships Chromium.app and the Playwright driver as data files,
    which loses the Unix execute bit on macOS. Downloaded files also carry the
    com.apple.quarantine extended attribute, causing Gatekeeper to block execution.
    This function handles both problems together.
    """
    if not _IS_MAC or not target.exists():
        return

    try:
        subprocess.run(
            ["xattr", "-cr", str(target)],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        pass

    fixed = 0
    for fp in target.rglob("*"):
        if not fp.is_file() or os.access(str(fp), os.X_OK):
            continue
        if _is_native_executable(fp):
            try:
                fp.chmod(fp.stat().st_mode | 0o755)
                fixed += 1
            except OSError:
                pass
    if fixed:
        logger.info(f"[Browser] Fixed execute permissions for {fixed} binaries in {target.name}")


def _find_bundled_browser_executable() -> str | None:
    """Search the PyInstaller bundle directory for a bundled Chromium/Chrome executable.

    Search paths (in priority order):
    1. {base}/browser/...                                       — direct bundle location
    2. {base}/playwright-browsers/chromium-*/chrome-win/         — Playwright (Windows)
    3. {base}/playwright-browsers/chromium-*/chrome-mac[-arm64]/ — Playwright (macOS)
    4. {base}/playwright-browsers/chromium-*/chrome-linux/       — Playwright (Linux)
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

    system = platform.system()
    is_win = system == "Windows"
    is_mac = system == "Darwin"

    if is_win:
        exe_name = "chrome.exe"
        headless_name = "headless_shell.exe"
    else:
        exe_name = "chrome"
        headless_name = "headless_shell"

    candidates: list[Path] = []
    for root in search_roots:
        if is_mac:
            candidates.append(root / "browser" / "Chromium.app" / "Contents" / "MacOS" / "Chromium")
        candidates.append(root / "browser" / exe_name)

        for pw_name in ("playwright-browsers", "playwright-browser"):
            pw_dir = root / pw_name
            if not pw_dir.is_dir():
                continue
            for chromium_dir in sorted(pw_dir.glob("chromium-*"), reverse=True):
                if is_win:
                    for win_dir in ("chrome-win", "chrome-win64"):
                        candidates.append(chromium_dir / win_dir / exe_name)
                elif is_mac:
                    for mac_dir in ("chrome-mac-arm64", "chrome-mac"):
                        candidates.append(
                            chromium_dir
                            / mac_dir
                            / "Chromium.app"
                            / "Contents"
                            / "MacOS"
                            / "Chromium"
                        )
                        candidates.append(chromium_dir / mac_dir / headless_name)
                else:
                    candidates.append(chromium_dir / "chrome-linux" / exe_name)
                    candidates.append(chromium_dir / "chrome-linux" / headless_name)

        candidates.append(root / "browser" / headless_name)

    for path in candidates:
        if not path.is_file():
            continue

        # macOS: fix the entire .app bundle or containing directory
        if is_mac:
            fix_root = next(
                (p for p in path.parents if p.suffix == ".app"),
                path.parent,
            )
            _ensure_macos_executability(fix_root)
        elif not is_win and not os.access(str(path), os.X_OK):
            try:
                path.chmod(path.stat().st_mode | 0o755)
                logger.info(f"[Browser] Fixed execute permission: {path}")
            except OSError as e:
                logger.warning(f"[Browser] Cannot set execute permission for {path}: {e}")
                continue

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


class _IsolatedBrowserContext:
    """Lightweight wrapper around a dedicated BrowserContext for parallel sub-agents.

    Implements the same minimal interface as BrowserManager so that
    PlaywrightTools / BrowserUseRunner can work unchanged.
    """

    def __init__(self, parent: BrowserManager, context: Any, page: Any):
        self._parent = parent
        self._context = context
        self._page = page
        self.state = BrowserState.READY
        self.visible = parent.visible
        self.using_user_chrome = parent.using_user_chrome

    @property
    def page(self) -> Any | None:
        return self._page

    @property
    def context(self) -> Any | None:
        return self._context

    @property
    def cdp_url(self) -> str | None:
        return self._parent.cdp_url

    @property
    def is_ready(self) -> bool:
        return self.state == BrowserState.READY

    @property
    def current_url(self) -> str | None:
        return self._page.url if self._page else None

    async def ensure_ready(self, visible: bool = True) -> bool:
        return self.state == BrowserState.READY

    async def start(self, visible: bool = True) -> bool:
        return True

    async def stop(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        self._context = None
        self._page = None
        self.state = BrowserState.IDLE

    async def get_status(self) -> dict:
        if not self._page:
            return {"is_open": False, "state": "idle"}
        try:
            return {
                "is_open": True,
                "state": "ready",
                "visible": self.visible,
                "tab_count": len(self._context.pages) if self._context else 0,
                "current_tab": {
                    "url": self._page.url,
                    "title": await self._page.title(),
                },
                "isolated": True,
            }
        except Exception as e:
            return {"is_open": True, "state": "ready", "error": str(e)}


class BrowserManager:
    """Browser lifecycle management (state machine + multi-strategy launch + fallback chain)"""

    def __init__(self, cdp_port: int = 9222, use_user_chrome: bool = True):
        self._cdp_port = cdp_port
        self._use_user_chrome = use_user_chrome

        # Playwright resources
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None

        # Public state
        self.state = BrowserState.IDLE
        self.visible: bool = True
        self.using_user_chrome: bool = False

        self._cdp_url: str | None = None
        self._last_successful_strategy: StartupStrategy | None = None
        self._startup_errors: list[str] = []
        self._startup_lock = asyncio.Lock()
        self._is_server = _is_server_environment()

        # Chrome detection
        from .chrome_finder import detect_chrome_installation

        self._chrome_path, self._chrome_user_data = detect_chrome_installation()

        # Bundled Chromium detection (PyInstaller packaging environment)
        self._bundled_executable = _find_bundled_browser_executable()

        if self._is_server:
            logger.info("[Browser] Server environment detected, will use extra launch args")

    # ── Driver health helpers ────────────────────────────────────

    @staticmethod
    def _is_driver_pipe_broken(error: Exception) -> bool:
        """Check whether the error indicates that the Playwright driver pipe is broken and needs a restart.

        When Chrome attempts to start but its process crashes (e.g. profile is locked),
        Playwright's pipe communication is severed. In that case ``_is_driver_dead()``
        may return False because it only checks cached attributes, while real communication
        is already unusable.
        """
        return "connection closed while reading from the driver" in str(error).lower()

    @staticmethod
    def _is_chrome_process_running() -> bool:
        """Check whether a Chrome main process is running."""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return '"chrome.exe"' in result.stdout.lower()
            else:
                result = subprocess.run(
                    ["pgrep", "-x", "chrome"],
                    capture_output=True,
                    timeout=5,
                )
                return result.returncode == 0
        except Exception:
            return False

    # ── Public properties ────────────────────────────────────────

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

    # ── Start / stop ────────────────────────────────────

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
                    if self._is_driver_pipe_broken(e) or await self._is_driver_dead():
                        logger.warning(
                            "[Browser] Playwright driver died/pipe broken, "
                            "restarting before next strategy..."
                        )
                        await self._cleanup_playwright()
                        if not await self._start_playwright_driver():
                            break

            if not headless:
                logger.info(
                    "[Browser] All headed strategies failed, restarting driver for headless retry..."
                )
                await self._cleanup_playwright()
                if not await self._start_playwright_driver():
                    logger.error("[Browser] Cannot restart Playwright driver for headless fallback")
                else:
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
                            if self._is_driver_pipe_broken(e) or await self._is_driver_dead():
                                logger.warning(
                                    f"[Browser] Driver dead/pipe broken after "
                                    f"{strategy.value}, restarting..."
                                )
                                await self._cleanup_playwright()
                                if not await self._start_playwright_driver():
                                    break
                            logger.warning(
                                f"[Browser] Headless fallback {strategy.value} also failed: {e}"
                            )

            logger.error(f"[Browser] All strategies failed: {'; '.join(self._startup_errors)}")
            self.state = BrowserState.ERROR
            await self._cleanup_playwright()
            return False

    async def _start_playwright_driver(self) -> bool:
        """Start the Playwright driver process (up to 2 retries). Returns False on failure."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("playwright")
            logger.error(f"Playwright import failed: {hint}")
            self.state = BrowserState.ERROR
            return False

        if _IS_MAC:
            try:
                import playwright as _pw_pkg

                _ensure_macos_executability(Path(_pw_pkg.__file__).parent / "driver")
            except Exception:
                pass

        max_attempts = 2
        last_err = ""

        for attempt in range(1, max_attempts + 1):
            try:
                pw_ctx = async_playwright()
                self._playwright = await asyncio.wait_for(
                    pw_ctx.start(),
                    timeout=20,
                )
                return True
            except (asyncio.TimeoutError, TimeoutError):
                last_err = f"Playwright driver start timed out (20s, attempt {attempt}/{max_attempts})"
                logger.warning(f"[Browser] {last_err}")
                await self._cleanup_playwright()
                if attempt < max_attempts:
                    await asyncio.sleep(1)
            except Exception as e:
                last_err = f"Playwright driver start failed: {type(e).__name__}: {e}"
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
        """Auto-start the browser if not ready; otherwise run a health check."""
        if self.state == BrowserState.READY:
            if await self._health_check():
                return True
            logger.warning("[Browser] Health check failed, restarting...")
            await self.stop()

        return await self.start(visible=self.visible)

    async def get_status(self) -> dict:
        """Return full status info for browser_open / browser_status to use."""
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

    async def create_isolated_context(self) -> BrowserManager:
        """Create a lightweight isolated browser context for parallel sub-agents.

        Returns a new BrowserManager-like wrapper that has its own BrowserContext
        and Page, avoiding tab/page crosstalk between concurrent agents.
        Only works when the main browser is READY with a _browser object that
        supports new_context() (CDP or standard launch — NOT persistent_context).
        """
        if self.state != BrowserState.READY:
            await self.ensure_ready()

        if self._browser and hasattr(self._browser, "new_context"):
            new_ctx = await self._browser.new_context()
            new_page = await new_ctx.new_page()

            isolated = _IsolatedBrowserContext(
                parent=self,
                context=new_ctx,
                page=new_page,
            )
            return isolated

        return self

    async def reset_state(self) -> None:
        """Clear references only, without closing resources (used when the browser is detected as externally closed)."""
        self.state = BrowserState.IDLE
        self._browser = None
        self._context = None
        self._page = None
        self.using_user_chrome = False
        self._cdp_url = None
        logger.info("[Browser] State reset")

    # ── Internal ────────────────────────────────────────────

    def _setup_browsers_path(self) -> None:
        """Set the PLAYWRIGHT_BROWSERS_PATH environment variable.

        If a bundled binary has already been located via _find_bundled_browser_executable(),
        this variable isn't needed because it'll be specified directly through the executable_path argument.
        """
        if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
            return

        # If we have a bundled executable, skip — it will be passed via executable_path in _try_bundled_chromium
        if self._bundled_executable:
            logger.info(
                f"[Browser] Will use bundled executable directly: {self._bundled_executable}"
            )
            return

        from openakita.runtime_env import IS_FROZEN

        if IS_FROZEN:
            import sys

            _meipass = getattr(sys, "_MEIPASS", None)
            if _meipass:
                for pw_name in ("playwright-browsers", "playwright-browser"):
                    bundled = Path(_meipass) / pw_name
                    if bundled.is_dir():
                        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
                        logger.info(f"[Browser] Using bundled {pw_name}: {bundled}")
                        return

        _root = os.environ.get("OPENAKITA_ROOT", "").strip()
        _base = Path(_root) if _root else Path.home() / ".openakita"
        browsers_dir = _base / "modules" / "browser" / "browsers"
        if browsers_dir.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
            logger.info(f"[Browser] Using external Chromium: {browsers_dir}")

    def _build_strategy_order(self) -> list[StartupStrategy]:
        """Decide attempt order based on the historically successful strategy."""
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
        """Try to connect to a running Chrome debug port."""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://localhost:{self._cdp_port}/json/version",
                timeout=2.0,
            )
            if response.status_code != 200:
                return False

        logger.info(f"[Browser] Found Chrome at localhost:{self._cdp_port}")

        self._browser = await asyncio.wait_for(
            self._playwright.chromium.connect_over_cdp(f"http://localhost:{self._cdp_port}"),
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
        """Build the Chromium launch argument list."""
        args = list(_COMMON_CHROMIUM_ARGS)
        args.append(f"--remote-debugging-port={self._cdp_port}")
        if self._is_server:
            args.extend(_SERVER_EXTRA_ARGS)
        return args

    async def _try_user_chrome(self, headless: bool, *, use_oa_profile: bool) -> bool:
        """Launch a persistent context using the user's Chrome."""
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
            if self._is_chrome_process_running():
                raise RuntimeError(
                    "Chrome is already running — user profile is locked by "
                    "the running instance, skipping to avoid driver pipe break"
                )
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
        """Preflight-check whether the Chromium binary is usable. Returns an error message or None."""
        try:
            exe = self._playwright.chromium.executable_path
        except Exception:
            return None

        if not exe:
            return None

        exe_path = Path(exe)
        if not exe_path.exists():
            hint = f"Chromium executable not found: {exe}\nPlease run: playwright install chromium"
            browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "(default)")
            logger.error(
                f"[Browser] Chromium preflight FAIL: {hint} "
                f"(PLAYWRIGHT_BROWSERS_PATH={browsers_path})"
            )
            return hint

        if platform.system() != "Windows":
            if _IS_MAC:
                fix_root = next(
                    (p for p in exe_path.parents if p.suffix == ".app"),
                    exe_path.parent,
                )
                _ensure_macos_executability(fix_root)
            elif not os.access(exe, os.X_OK):
                try:
                    exe_path.chmod(exe_path.stat().st_mode | 0o755)
                    logger.info(f"[Browser] Fixed execute permission for Chromium: {exe}")
                except OSError:
                    return f"Chromium executable has no execute permission: {exe}"

        file_size = exe_path.stat().st_size
        if file_size < 1_000_000:
            hint = f"Chromium binary appears invalid (only {file_size} bytes); download may be incomplete: {exe}"
            logger.error(f"[Browser] {hint}")
            return hint

        logger.info(f"[Browser] Chromium binary verified: {exe} ({file_size / 1024 / 1024:.1f} MB)")
        return None

    async def _try_bundled_chromium(self, headless: bool) -> bool:
        """Launch using Chromium.

        Strategies (in order):
        1. persistent_context — atomically create browser + context + page, avoid inter-process crashes
        2. launch + new_context + new_page — traditional fallback
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
            f"[Browser] Launching Chromium (headless={effective_headless}, exe={exe_label})"
        )

        last_err: Exception | None = None

        # --- Strategy 1: persistent_context (atomic launch, avoid new_page crashes) ---
        try:
            ok = await self._launch_persistent(exe_path, effective_headless)
            if ok:
                return True
        except Exception as e:
            last_err = e
            logger.info(f"[Browser] persistent_context failed ({e}), trying standard launch...")
            await self._close_browser_silently()

        # --- Strategy 2: traditional launch + new_context + new_page ---
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
        self,
        exe_path: str | None,
        headless: bool,
    ) -> bool:
        """Atomically launch browser + page using launch_persistent_context."""
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
        logger.info(f"Browser started with Chromium persistent_context (visible={self.visible})")
        return True

    async def _launch_standard(
        self,
        exe_path: str | None,
        headless: bool,
    ) -> bool:
        """Traditional launch + new_context + new_page."""
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
        """Close browser resources without cleaning up Playwright driver."""
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
        """Quick health check for browser connection status."""
        try:
            if not self._page or not self._context:
                return False
            _ = self._page.url
            _ = self._context.pages
            return True
        except Exception:
            return False

    async def _stop_internal(self) -> None:
        """Internal stop sequence (caller must hold lock)."""
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

    async def _is_driver_dead(self) -> bool:
        """Check if Playwright driver process has crashed."""
        if not self._playwright:
            return True
        try:
            _ = self._playwright.chromium.executable_path
            return False
        except Exception:
            return True

    async def _cleanup_playwright(self) -> None:
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
