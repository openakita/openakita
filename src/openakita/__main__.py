"""
OpenAkita package entry point — supports `python -m openakita` invocation.
Also serves as the PyInstaller entry point.
"""

import openakita._ensure_utf8  # noqa: F401

# Before importing any business modules, inject optional module paths and fix SSL cert paths (frozen-environment fallback)
from openakita.runtime_env import IS_FROZEN, ensure_ssl_certs, inject_module_paths

if IS_FROZEN:
    ensure_ssl_certs()
    inject_module_paths()

from openakita.main import app

if __name__ == "__main__":
    app()
