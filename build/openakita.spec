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
    # Other large packages not needed
    "matplotlib",
    "scipy",
    "numpy.testing",
    "pandas",
    "PIL",
    "tkinter",
    "unittest",
    "test",
    "tests",
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

# Built-in system skills
skills_dir = PROJECT_ROOT / "skills" / "system"
if skills_dir.exists():
    datas.append((str(skills_dir), "openakita/builtin_skills/system"))

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

# On macOS, remove conflicting Python file before COLLECT creates symlink
# This is a workaround for PyInstaller bug with Python.framework symlinks
import sys as _sys
_binaries_filtered = a.binaries
if _sys.platform == "darwin":
    # Remove the Python file that conflicts with symlink creation
    _internal_python = PROJECT_ROOT / "dist" / "openakita-server" / "_internal" / "Python"
    if _internal_python.exists():
        print(f"[spec] Removing conflicting file: {_internal_python}")
        _internal_python.unlink()
    
    # Also try to remove the entire _internal directory if it exists
    _internal_dir = PROJECT_ROOT / "dist" / "openakita-server" / "_internal"
    if _internal_dir.exists():
        print(f"[spec] Removing _internal directory: {_internal_dir}")
        shutil.rmtree(_internal_dir)
    
    print(f"[spec] macOS cleanup completed, binaries count: {len(a.binaries)}")

coll = COLLECT(
    exe,
    _binaries_filtered,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="openakita-server",
)
