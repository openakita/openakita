# -*- mode: python ; coding: utf-8 -*-
"""
OpenAkita PyInstaller spec file

Usage:
  Core package: pyinstaller build/openakita.spec  (excludes heavy dependencies by default)
  Full package: OPENAKITA_BUILD_MODE=full pyinstaller build/openakita.spec

Environment variables:
  OPENAKITA_BUILD_MODE: "core" (default) or "full"
"""

import os
import sys
import shutil
from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(SPECPATH).parent
SRC_DIR = PROJECT_ROOT / "src"

# Force clean output directories to avoid macOS symlink conflicts
# This must happen early, before PyInstaller starts collecting files
_dist_server = PROJECT_ROOT / "dist" / "openakita-server"
if _dist_server.exists():
    print(f"[spec] Removing existing output: {_dist_server}")
    shutil.rmtree(_dist_server)

# Build mode
BUILD_MODE = os.environ.get("OPENAKITA_BUILD_MODE", "core")

# ============== Hidden Imports ==============
# Dynamic imports that PyInstaller static analysis may miss

hidden_imports_core = [
    # -- openakita internal modules --
    "openakita",
    "openakita.main",
    "openakita.config",
    "openakita.runtime_env",
    "openakita.core.agent",
    "openakita.core.llm",
    "openakita.core.tools",
    "openakita.memory",
    "openakita.memory.manager",
    "openakita.memory.vector_store",
    "openakita.memory.daily_consolidator",
    "openakita.memory.consolidator",
    "openakita.channels",
    "openakita.channels.gateway",
    "openakita.channels.base",
    "openakita.channels.types",
    "openakita.channels.adapters",
    "openakita.channels.adapters.telegram",
    "openakita.channels.adapters.feishu",
    "openakita.channels.adapters.dingtalk",
    "openakita.channels.adapters.onebot",
    "openakita.channels.adapters.qq_official",
    "openakita.channels.adapters.wework_bot",
    "openakita.channels.media",
    "openakita.channels.media.handler",
    "openakita.channels.media.audio_utils",
    "openakita.channels.media.storage",
    "openakita.skills",
    "openakita.skills.loader",
    "openakita.evolution",
    "openakita.evolution.installer",
    "openakita.setup_center",
    "openakita.setup_center.bridge",
    "openakita.orchestration",
    "openakita.orchestration.bus",
    "openakita.tracing",
    "openakita.logging",
    "openakita.tools",
    "openakita.tools.shell",
    "openakita.tools._import_helper",
    # -- LLM registries (dynamically imported via import_module, PyInstaller can't trace) --
    "openakita.llm.registries",
    "openakita.llm.registries.base",
    "openakita.llm.registries.anthropic",
    "openakita.llm.registries.openai",
    "openakita.llm.registries.dashscope",
    "openakita.llm.registries.kimi",
    "openakita.llm.registries.minimax",
    "openakita.llm.registries.deepseek",
    "openakita.llm.registries.openrouter",
    "openakita.llm.registries.siliconflow",
    "openakita.llm.registries.volcengine",
    "openakita.llm.registries.zhipu",
    "openakita.llm.capabilities",
    # -- Third-party core dependencies --
    "uvicorn",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "fastapi",
    "pydantic",
    "pydantic_settings",
    "anthropic",
    "openai",
    "httpx",
    "aiofiles",
    "aiosqlite",
    "yaml",
    "dotenv",
    "tenacity",
    "typer",
    "rich",
    "git",
    "mcp",
    "nest_asyncio",
    # -- Lightweight runtime dependencies (frequently used, small footprint) --
    "ddgs",                     # DuckDuckGo search (~2MB)
    "ddgs.engines",             # ddgs 搜索引擎模块 (pkgutil 动态发现)
    "ddgs.engines.bing",
    "ddgs.engines.brave",
    "ddgs.engines.duckduckgo",
    "ddgs.engines.duckduckgo_images",
    "ddgs.engines.duckduckgo_news",
    "ddgs.engines.duckduckgo_videos",
    "ddgs.engines.google",
    "ddgs.engines.grokipedia",
    "ddgs.engines.mojeek",
    "ddgs.engines.wikipedia",
    "ddgs.engines.yahoo",
    "ddgs.engines.yahoo_news",
    "ddgs.engines.yandex",
    "ddgs.engines.annasarchive",
    "primp",                    # ddgs HTTP 客户端 (Rust .pyd)
    "lxml",                     # ddgs HTML 解析
    "lxml.html",
    "lxml.etree",
    "fake_useragent",           # ddgs 随机 User-Agent
    "h2",                       # ddgs HTTP/2 支持
    "httpcore",                 # httpx 传输层
    "psutil",                   # Process info (~1MB)
    "pyperclip",                # Clipboard (~50KB)
    "websockets",               # WebSocket protocol (~500KB)
    "aiohttp",                  # Async HTTP server (~2MB, used by wework/qq webhook)
    "aiohttp.web",
    # -- Document processing (skill dependencies, bundled directly) --
    "docx",                     # python-docx: Word files (~1MB)
    "openpyxl",                 # Excel files (~5MB)
    "pptx",                     # python-pptx: PowerPoint files (~3MB)
    "fitz",                     # PyMuPDF: PDF files (~15MB)
    "pypdf",                    # pypdf: PDF fallback (~2MB)
    # -- Image processing --
    "PIL",                      # Pillow: image format conversion (~10MB)
    # -- Desktop automation (Windows) --
    "pyautogui",                # Mouse/keyboard control (~2MB)
    "pywinauto",                # Windows UI Automation (~5MB)
    "pywinauto.controls",
    "pywinauto.controls.uiawrapper",
    "mss",                      # Screenshot capture (~1MB)
    "mss.tools",
    # -- IM channel adapters (small, bundled to avoid install-on-config bugs) --
    "lark_oapi",                # Feishu/Lark (~3MB)
    "dingtalk_stream",          # DingTalk Stream (~2MB)
    "Crypto",                   # pycryptodome for WeWork (~3MB)
    "Crypto.Cipher",
    "Crypto.Cipher.AES",
    "botpy",                    # QQ Bot (~5MB)
]

hidden_imports_full = [
    # -- Heavy optional dependencies (full package only) --
    "sentence_transformers",
    "chromadb",
    "torch",
    "playwright",
    "zmq",
    "whisper",
]

hidden_imports = hidden_imports_core
if BUILD_MODE == "full":
    hidden_imports += hidden_imports_full

# ============== Excludes ==============
# Heavy dependencies excluded from core package

excludes_core = [
    "sentence_transformers",
    "chromadb",
    "torch",
    "torchvision",
    "torchaudio",
    "playwright",
    "zmq",
    "pyzmq",
    "whisper",
    "browser_use",
    "langchain",
    "langchain_openai",
    # Heavy packages not needed for core (often pulled in from global site-packages)
    "cv2",                  # OpenCV (~122MB) — not a core dependency
    "opencv_python",
    # NOTE: numpy and PIL removed from excludes — many optional modules
    # (e.g. Pillow, mss, pyautogui) depend on them indirectly; excluding
    # causes silent cascading ImportErrors at runtime.
    "matplotlib",
    "scipy",
    "pandas",
    "psycopg2",             # PostgreSQL driver — not a core dependency
    "psycopg2_binary",
    # GUI toolkits (not needed for headless server)
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    # Test frameworks
    "unittest",
    "test",
    "tests",
    "pytest",
    "_pytest",
]

excludes = excludes_core if BUILD_MODE == "core" else []

# ============== Data Files ==============
# Non-Python files to be bundled

datas = []

# rich._unicode_data: filename contains hyphen (unicode17-0-0.py), PyInstaller cannot
# handle via hidden_imports, must be copied as data file
import rich._unicode_data as _rud
_rud_dir = str(Path(_rud.__file__).parent)
datas.append((_rud_dir, "rich/_unicode_data"))

# Provider list (single source of truth, shared by frontend and backend)
# Must be bundled to openakita/llm/registries/ directory, Python reads via Path(__file__).parent
providers_json = SRC_DIR / "openakita" / "llm" / "registries" / "providers.json"
if providers_json.exists():
    datas.append((str(providers_json), "openakita/llm/registries"))

# pyproject.toml (version source, after bundling __init__.py reads via relative path)
# After PyInstaller bundling, openakita module is in _internal/, pyproject.toml would be 3 levels up
# In bundled mode this path won't work, so we write a version file directly
_pyproject_path = PROJECT_ROOT / "pyproject.toml"
if _pyproject_path.exists():
    import tomllib
    with open(_pyproject_path, "rb") as _f:
        _pyproject_version = tomllib.load(_f)["project"]["version"]
    # Write a simple version file to bundle directory
    _version_file = SRC_DIR / "openakita" / "_bundled_version.txt"
    _version_file.write_text(_pyproject_version, encoding="utf-8")
    datas.append((str(_version_file), "openakita"))

# Built-in Python interpreter + pip (bundled mode can install optional modules without host Python)
# Bundle system python.exe and pip module to _internal/, Rust side discovers via find_pip_python
import shutil
_sys_python_exe = Path(sys.executable)
if _sys_python_exe.exists():
    datas.append((str(_sys_python_exe), "."))  # python.exe -> _internal/

# pip and its dependencies (minimal set needed for pip install)
import pip
_pip_dir = str(Path(pip.__file__).parent)
datas.append((_pip_dir, "pip"))

# pip vendor dependencies (pip._vendor contains requests, urllib3 etc.)
# Already included in pip directory, no extra handling needed

# Built-in system skills (64 core skills: tool wrappers, memory, planning, etc.)
skills_dir = PROJECT_ROOT / "skills" / "system"
if skills_dir.exists():
    datas.append((str(skills_dir), "openakita/builtin_skills/system"))

# External/extended skills (29 skills: document generation, browser testing, etc.)
# These are discovered at runtime via SKILL_DIRECTORIES → "skills" relative to project_root
# In bundled mode, _builtin_skills_root() resolves to _internal/openakita/builtin_skills/
# so we place external skills alongside system skills
_skills_root = PROJECT_ROOT / "skills"
if _skills_root.exists():
    for _skill_entry in _skills_root.iterdir():
        if _skill_entry.is_dir() and _skill_entry.name != "system" and _skill_entry.name != ".gitkeep":
            datas.append((str(_skill_entry), f"openakita/builtin_skills/{_skill_entry.name}"))

# ============== Analysis ==============

a = Analysis(
    [str(SRC_DIR / "openakita" / "__main__.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

import sys as _sys

# On macOS, use onefile mode to avoid COLLECT symlink issues with Python.framework
# On other platforms, use onedir mode for faster startup
if _sys.platform == "darwin":
    # macOS: bundle everything into single executable
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,  # Include binaries in EXE for onefile mode
        a.datas,     # Include datas in EXE for onefile mode
        [],
        name="openakita-server",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,  # Disable UPX on macOS for stability
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    # onefile mode outputs directly to distpath, no COLLECT needed
    # build_backend.py will move it to the expected directory structure
else:
    # Windows/Linux: use onedir mode with COLLECT
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="openakita-server",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="openakita-server",
    )
