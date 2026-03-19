"""
UTF-8 编码强制模块 — 在所有入口点最早期导入

解决 Windows 上 sys.stdout/stderr 默认使用 GBK 编码，
导致中文、emoji 等 Unicode 字符输出乱码或崩溃的问题。

用法: 在每个入口模块的最顶部添加:
    import openakita._ensure_utf8  # noqa: F401
"""

import os
import sys


def ensure_utf8_stdio() -> None:
    """将 stdout/stderr 重新配置为 UTF-8 编码。

    仅在流对象支持 reconfigure 时生效（CPython 3.7+）。
    errors="replace" 确保遇到无法编码的字符时用替代符号而非崩溃。
    """
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


if sys.platform == "win32":
    ensure_utf8_stdio()

# 确保子进程也继承 UTF-8 编码设置
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# PyInstaller 打包环境下，某些第三方包的 METADATA 文件含非 UTF-8 字节，
# pydantic 导入时通过 importlib.metadata.entry_points() 扫描插件会触发
# UnicodeDecodeError。本项目不使用 pydantic 插件，直接禁用即可。
if getattr(sys, "frozen", False):
    os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")

# Windows 环境下预填充 platform 缓存，避免后续 platform.system() 等调用
# 通过 subprocess 执行 `cmd /c ver` 触发阻塞（在某些环境中 cmd 子进程会卡死）。
if sys.platform == "win32":
    import platform as _platform
    try:
        _wv = sys.getwindowsversion()
        _platform._uname_cache = _platform.uname_result(
            "Windows",
            os.environ.get("COMPUTERNAME", ""),
            str(_wv.major),
            f"{_wv.major}.{_wv.minor}.{_wv.build}",
            os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
        )
    except Exception:
        pass
