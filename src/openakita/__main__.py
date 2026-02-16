"""
OpenAkita 包入口点 - 支持 `python -m openakita` 调用
同时作为 PyInstaller 打包入口
"""

import sys

# Windows PyInstaller 打包环境下，stdout/stderr 可能使用 GBK 编码（即使设置了
# PYTHONUTF8=1，bootloader 也可能在该环境变量生效前已配置好 stdio 编码）。
# 当代码中有 emoji 或其他非 GBK 字符时会触发 UnicodeEncodeError。
# 强制将 stdio 重新配置为 UTF-8 + replace 错误策略，确保任何 print() 都不会崩溃。
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 在导入任何业务模块之前，注入可选模块路径（打包环境兜底）
from openakita.runtime_env import IS_FROZEN, inject_module_paths

if IS_FROZEN:
    inject_module_paths()

from openakita.main import app

if __name__ == "__main__":
    app()
