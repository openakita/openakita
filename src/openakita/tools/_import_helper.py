"""
统一导入辅助 — 可选依赖的 import 与友好提示

所有做延迟导入的模块统一使用本模块提供的 import_or_hint() 函数，
在 ImportError 时返回上下文相关的安装提示（打包环境→设置中心；开发环境→pip install）。

Usage:
    from openakita.tools._import_helper import import_or_hint

    hint = import_or_hint("playwright")
    if hint:
        return {"error": hint}

    # 此时 playwright 已可用，可以安全导入
    from playwright.async_api import async_playwright
"""

import importlib
import logging
from typing import Any

from openakita.runtime_env import IS_FROZEN

logger = logging.getLogger(__name__)

# ===== Import Name → (module_id, setup_center_display_name, pip_package) =====
# module_id=None 表示该包已打包进 core bundle，理论上不会缺失
# pip_package 为空串时使用 import name 作为 pip 包名

_PACKAGE_MODULE_MAP: dict[str, tuple[str | None, str | None, str]] = {
    # -- 已直接打包的包 (module_id=None，正常不应缺失) --
    "ddgs": (None, None, "ddgs"),
    "psutil": (None, None, "psutil"),
    "pyperclip": (None, None, "pyperclip"),
    "websockets": (None, None, "websockets"),
    "aiohttp": (None, None, "aiohttp"),
    "httpx": (None, None, "httpx"),
    "yaml": (None, None, "pyyaml"),
    "mcp": (None, None, "mcp"),
    # 文档处理 (已直接打包)
    "docx": (None, None, "python-docx"),
    "openpyxl": (None, None, "openpyxl"),
    "pptx": (None, None, "python-pptx"),
    "fitz": (None, None, "PyMuPDF"),
    "pypdf": (None, None, "pypdf"),
    # 图像处理 (已直接打包)
    "PIL": (None, None, "Pillow"),
    "Pillow": (None, None, "Pillow"),
    # 桌面自动化 (已直接打包)
    "pyautogui": (None, None, "pyautogui"),
    "pywinauto": (None, None, "pywinauto"),
    "mss": (None, None, "mss"),
    # -- 浏览器自动化 (重量级可选模块) --
    "playwright": ("browser", "浏览器自动化", "playwright"),
    "playwright.async_api": ("browser", "浏览器自动化", "playwright"),
    "browser_use": ("browser", "浏览器自动化", "browser-use"),
    "langchain_openai": ("browser", "浏览器自动化", "langchain-openai"),
    # -- 向量记忆 --
    "sentence_transformers": ("vector-memory", "向量记忆增强", "sentence-transformers"),
    "chromadb": ("vector-memory", "向量记忆增强", "chromadb"),
    # -- 语音识别 --
    "whisper": ("whisper", "语音识别", "openai-whisper"),
    "static_ffmpeg": ("whisper", "语音识别", "static-ffmpeg"),
    # -- 多 Agent 协同 --
    "zmq": ("orchestration", "多Agent协同", "pyzmq"),
    # -- IM 通道适配器 --
    "lark_oapi": ("im-feishu", "飞书通道", "lark-oapi"),
    "dingtalk_stream": ("im-dingtalk", "钉钉通道", "dingtalk-stream"),
    "Crypto": ("im-wework", "企业微信通道", "pycryptodome"),
    "Cryptodome": ("im-wework", "企业微信通道", "pycryptodome"),
    "botpy": ("im-qqbot", "QQ机器人", "qq-botpy"),
    "pilk": ("im-qqbot", "QQ机器人", "pilk"),
    # -- 其他 --
    "telegram": (None, None, "python-telegram-bot"),
    "pytesseract": (None, None, "pytesseract"),
    "nacl": (None, None, "PyNaCl"),
    "modelscope": (None, None, "modelscope"),
}


def import_or_hint(package: str) -> str | None:
    """尝试导入包，成功返回 None，失败返回用户友好的安装提示。

    Args:
        package: Python 导入名（如 "playwright"、"ddgs"、"lark_oapi"）

    Returns:
        None 如果导入成功；否则返回安装提示字符串。
    """
    try:
        importlib.import_module(package)
        return None
    except ImportError:
        return _build_hint(package)


def _build_hint(package: str) -> str:
    """根据包名和运行环境构建安装提示。"""
    info = _PACKAGE_MODULE_MAP.get(package)

    if info is None:
        # 未知包，返回通用 pip 提示
        return f"缺少依赖: pip install {package}"

    module_id, display_name, pip_name = info

    if IS_FROZEN and module_id:
        return f"请在设置中心安装「{display_name}」模块后重启服务"
    elif IS_FROZEN and not module_id:
        # 已打包但仍缺失（异常情况）
        return f"核心依赖 {pip_name} 缺失，请尝试重新安装应用"
    else:
        # 开发环境
        return f"缺少依赖: pip install {pip_name}"


def try_import(package: str) -> tuple[Any | None, str | None]:
    """尝试导入包，返回 (module, None) 或 (None, hint)。

    便于在一行内完成导入检查：

        mod, hint = try_import("playwright")
        if hint:
            return {"error": hint}
        # 使用 mod ...
    """
    try:
        mod = importlib.import_module(package)
        return mod, None
    except ImportError:
        return None, _build_hint(package)


def check_imports(*packages: str) -> str | None:
    """检查多个包是否可导入，返回第一个缺失包的提示，或 None（全部可用）。

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
