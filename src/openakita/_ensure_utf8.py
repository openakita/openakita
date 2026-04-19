"""
UTF-8 encoding enforcement module — import at the very start of every entry point.

Solves the issue on Windows where sys.stdout/stderr default to GBK encoding,
causing Chinese characters, emoji, and other Unicode output to be garbled or crash.

Usage: add at the very top of each entry module:
    import openakita._ensure_utf8  # noqa: F401
"""

import os
import sys


def ensure_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to use UTF-8 encoding.

    Only takes effect when the stream object supports reconfigure (CPython 3.7+).
    errors="replace" ensures that unencodable characters are replaced with a
    substitution marker rather than raising an exception.
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

    # Set Windows console code page to UTF-8 (equivalent to chcp 65001)
    # Prevents emoji and similar characters from triggering GBK encoding errors when printed
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

# Ensure child processes also inherit UTF-8 encoding settings
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Under PyInstaller, some third-party METADATA files contain non-UTF-8 bytes.
# When pydantic imports, it scans plugins via importlib.metadata.entry_points(),
# which can trigger UnicodeDecodeError. This project does not use pydantic plugins,
# so disabling them is safe.
if getattr(sys, "frozen", False):
    os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")

# On Windows, pre-populate the platform cache to prevent later calls to
# platform.system() etc. from spawning `cmd /c ver` via subprocess, which can
# hang in certain environments.
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
