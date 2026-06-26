"""
OpenAkita 包入口点 - 支持 `python -m openakita` 调用
同时作为 PyInstaller 打包入口
"""

import openakita._ensure_utf8  # noqa: F401

# 在导入任何业务模块之前，注入可选模块路径 + 修正 SSL 证书路径（打包环境兜底）
from openakita.runtime_env import IS_FROZEN, ensure_ssl_certs, inject_module_paths

if IS_FROZEN:
    ensure_ssl_certs()
    inject_module_paths()

from openakita.main import app

if __name__ == "__main__":
    app()
