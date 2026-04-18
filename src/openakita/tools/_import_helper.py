"""
Unified import helper — lazy imports with friendly hints for optional dependencies

All modules that perform lazy imports use the import_or_hint() function provided
by this module. On ImportError it returns context-aware installation guidance
(packaged environment -> setup center; dev environment -> pip install).

Usage:
    from openakita.tools._import_helper import import_or_hint

    hint = import_or_hint("playwright")
    if hint:
        return {"error": hint}

    # playwright is now available and safe to import
    from playwright.async_api import async_playwright
"""

import importlib
import logging
from typing import Any

from openakita.runtime_env import IS_FROZEN

logger = logging.getLogger(__name__)

# ===== Import Name -> (module_id, setup_center_display_name, pip_package) =====
# module_id=None means the package is bundled into the core bundle and should not be missing
# When pip_package is empty, the import name is used as the pip package name

_PACKAGE_MODULE_MAP: dict[str, tuple[str | None, str | None, str]] = {
    # -- Directly bundled packages (module_id=None, normally should not be missing) --
    "ddgs": (None, None, "ddgs"),
    "psutil": (None, None, "psutil"),
    "pyperclip": (None, None, "pyperclip"),
    "websockets": (None, None, "websockets"),
    "aiohttp": (None, None, "aiohttp"),
    "httpx": (None, None, "httpx"),
    "yaml": (None, None, "pyyaml"),
    "mcp": (None, None, "mcp"),
    # Document processing (directly bundled)
    "docx": (None, None, "python-docx"),
    "openpyxl": (None, None, "openpyxl"),
    "pptx": (None, None, "python-pptx"),
    "fitz": (None, None, "PyMuPDF"),
    "pypdf": (None, None, "pypdf"),
    # Image processing (directly bundled)
    "PIL": (None, None, "Pillow"),
    "Pillow": (None, None, "Pillow"),
    # Desktop automation (directly bundled)
    "pyautogui": (None, None, "pyautogui"),
    "pywinauto": (None, None, "pywinauto"),
    "mss": (None, None, "mss"),
    # -- Browser automation (directly bundled) --
    "playwright": (None, None, "playwright"),
    "playwright.async_api": (None, None, "playwright"),
    # -- Vector memory --
    "sentence_transformers": ("vector-memory", "Vector Memory Enhancement", "sentence-transformers"),
    "chromadb": ("vector-memory", "Vector Memory Enhancement", "chromadb"),
    # -- Speech recognition --
    "whisper": ("whisper", "Speech Recognition", "openai-whisper"),
    "static_ffmpeg": ("whisper", "Speech Recognition", "static-ffmpeg"),
    # -- IM channel adapters (directly bundled) --
    "lark_oapi": (None, None, "lark-oapi"),
    "dingtalk_stream": (None, None, "dingtalk-stream"),
    "Crypto": (None, None, "pycryptodome"),
    "Cryptodome": (None, None, "pycryptodome"),
    "pilk": (None, None, "pilk"),
    # -- Other --
    "telegram": (None, None, "python-telegram-bot"),
    "pytesseract": (None, None, "pytesseract"),
    "nacl": (None, None, "PyNaCl"),
    "modelscope": (None, None, "modelscope"),
}


def import_or_hint(package: str) -> str | None:
    """Try to import a package; returns None on success, or a user-friendly installation hint on failure.

    Args:
        package: Python import name (e.g. "playwright", "ddgs", "lark_oapi")

    Returns:
        None if import succeeded; otherwise an installation hint string.
    """
    try:
        importlib.import_module(package)
        return None
    except ImportError as exc:
        logger.debug("import_or_hint: %s import failed: %s", package, exc, exc_info=True)
        return _build_hint(package)


def _build_hint(package: str) -> str:
    """Build an installation hint based on the package name and runtime environment."""
    info = _PACKAGE_MODULE_MAP.get(package)

    if info is None:
        # Unknown package, return generic pip hint
        return f"Missing dependency: pip install {package}"

    module_id, display_name, pip_name = info

    if IS_FROZEN and module_id:
        return f"Please install the \"{display_name}\" module in the setup center and restart the service"
    elif IS_FROZEN and not module_id:
        # Bundled but still missing (unusual situation)
        return f"Core dependency {pip_name} is missing, please try reinstalling the application"
    else:
        # Development environment
        return f"Missing dependency: pip install {pip_name}"


def try_import(package: str) -> tuple[Any | None, str | None]:
    """Try to import a package, returning (module, None) or (None, hint).

    Convenient for completing an import check in a single line:

        mod, hint = try_import("playwright")
        if hint:
            return {"error": hint}
        # Use mod ...
    """
    try:
        mod = importlib.import_module(package)
        return mod, None
    except ImportError:
        return None, _build_hint(package)


def check_imports(*packages: str) -> str | None:
    """Check whether multiple packages are importable; returns a hint for the first missing package, or None if all are available.

    Usage:
        hint = check_imports("pyautogui", "pywinauto", "mss")
        if hint:
            return {"error": hint}
    """
    for pkg in packages:
        hint = import_or_hint(pkg)
        if hint:
            return hint
    return None
