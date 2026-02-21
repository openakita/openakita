"""
Chrome 检测与 Profile 管理

提供 Chrome 安装检测、OpenAkita 专用 profile 管理、Cookie 同步等工具函数。
从原 browser_mcp.py 提取，供 BrowserManager 启动流程使用。
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
    检测系统上的 Chrome 安装

    Returns:
        (executable_path, user_data_dir) - 如果找到 Chrome
        (None, None) - 如果未找到
    """
    system = platform.system()

    if system == "Windows":
        chrome_paths = [
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files"))
            / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
            / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google" / "Chrome" / "Application" / "chrome.exe",
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
    获取 OpenAkita 专用的 Chrome profile 目录。
    独立于用户的 Chrome，可以在用户 Chrome 运行时使用。
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
    同步用户 Chrome 的 cookies 到 OpenAkita profile。
    只复制关键文件以保持登录状态。
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
    """检测 Chrome DevTools MCP 是否可用"""
    result: dict[str, Any] = {
        "available": False,
        "npx_available": False,
        "chrome_available": False,
        "suggestion": "",
    }

    npx_path = shutil.which("npx")
    result["npx_available"] = npx_path is not None

    chrome_path, _ = detect_chrome_installation()
    result["chrome_available"] = chrome_path is not None

    if result["npx_available"] and result["chrome_available"]:
        result["available"] = True
        result["suggestion"] = (
            "Chrome DevTools MCP 可用。建议在 Chrome 中访问 chrome://inspect/#remote-debugging "
            "开启远程调试，以便 AI Agent 连接到您的浏览器（保留登录状态和密码管理器）。"
        )
    elif not result["npx_available"]:
        result["suggestion"] = "Chrome DevTools MCP 需要 Node.js。请安装 Node.js v20.19+ 以启用此功能。"
    elif not result["chrome_available"]:
        result["suggestion"] = "未检测到 Chrome 浏览器。请安装 Chrome 以使用 Chrome DevTools MCP。"

    return result


async def check_mcp_chrome_extension(port: int = 12306, timeout: float = 2.0) -> bool:
    """检测 mcp-chrome 扩展是否正在运行"""
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            await client.get(f"http://127.0.0.1:{port}/mcp", timeout=timeout)
            return True
    except Exception:
        return False
