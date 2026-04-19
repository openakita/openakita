"""
Chrome Detection and Profile Management

Provides utilities for Chrome installation detection, OpenAkita-specific profile
management, cookie syncing, and more. Extracted from the original browser_mcp.py
for use by BrowserManager startup flow.
"""

import logging
import os
import platform
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def detect_chrome_installation() -> tuple[str | None, str | None]:
    """
    Detect Chrome installation on the system.

    Returns:
        (executable_path, user_data_dir) - if Chrome is found
        (None, None) - if not found
    """
    system = platform.system()

    if system == "Windows":
        chrome_paths = [
            Path(
                os.environ.get(
                    "PROGRAMFILES", os.environ.get("SYSTEMDRIVE", "C:") + "\\Program Files"
                )
            )
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(
                os.environ.get(
                    "PROGRAMFILES(X86)",
                    os.environ.get("SYSTEMDRIVE", "C:") + "\\Program Files (x86)",
                )
            )
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

    elif system == "Darwin":
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
    Get the OpenAkita-specific Chrome profile directory.
    Independent of the user's Chrome, can be used while user Chrome is running.
    """
    import tempfile

    system = platform.system()
    if system == "Windows":
        base_dir = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    elif system == "Darwin":
        base_dir = Path.home() / "Library" / "Application Support"
    else:
        base_dir = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))

    profile_dir = base_dir / "OpenAkita" / "ChromeProfile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    return str(profile_dir)


def sync_chrome_cookies(src_user_data: str, dst_profile: str) -> bool:
    """
    Sync cookies from the user's Chrome to the OpenAkita profile.
    Only copies key files to preserve login state.
    """
    src_default = Path(src_user_data) / "Default"
    dst_default = Path(dst_profile) / "Default"

    if not src_default.exists():
        logger.warning(f"[CookieSync] Source Default profile not found: {src_default}")
        return False

    dst_default.mkdir(parents=True, exist_ok=True)

    important_files = [
        "Cookies",
        "Login Data",
        "Web Data",
        "Preferences",
        "Secure Preferences",
        "Local State",
    ]

    copied = 0
    for filename in important_files:
        src_file = src_default / filename
        if src_file.exists():
            try:
                dst_file = dst_default / filename
                if not dst_file.exists() or src_file.stat().st_mtime > dst_file.stat().st_mtime:
                    shutil.copy2(src_file, dst_file)
                    copied += 1
            except Exception as e:
                logger.warning(f"[CookieSync] Failed to copy {filename}: {e}")

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
    """Check whether Chrome DevTools MCP is available."""
    result: dict[str, Any] = {
        "available": False,
        "npx_available": False,
        "chrome_available": False,
        "suggestion": "",
    }

    from ...utils.path_helper import which_command

    npx_path = which_command("npx")
    result["npx_available"] = npx_path is not None

    chrome_path, _ = detect_chrome_installation()
    result["chrome_available"] = chrome_path is not None

    if result["npx_available"] and result["chrome_available"]:
        result["available"] = True
        result["suggestion"] = (
            "Chrome DevTools MCP is available. Open chrome://inspect/#remote-debugging in "
            "Chrome to enable remote debugging so the AI Agent can connect to your browser "
            "(preserving login state and password manager)."
        )
    elif not result["npx_available"]:
        result["suggestion"] = (
            "Chrome DevTools MCP requires Node.js. Please install Node.js v20.19+ to enable this feature."
        )
    elif not result["chrome_available"]:
        result["suggestion"] = "Chrome not detected. Please install Chrome to use Chrome DevTools MCP."

    return result


async def detect_chrome_cdp_port(
    ports: tuple[int, ...] = (9222, 9223, 9225),
    timeout: float = 2.0,
) -> int | None:
    """Probe local Chrome CDP debugging ports, returning the first available port or None."""
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            for port in ports:
                try:
                    resp = await client.get(
                        f"http://127.0.0.1:{port}/json/version",
                        timeout=timeout,
                    )
                    if resp.status_code == 200:
                        return port
                except Exception:
                    continue
    except ImportError:
        pass
    return None


async def check_mcp_chrome_extension(port: int = 12306, timeout: float = 2.0) -> bool:
    """Check whether the mcp-chrome extension is running."""
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            await client.get(f"http://127.0.0.1:{port}/mcp", timeout=timeout)
            return True
    except Exception:
        return False
