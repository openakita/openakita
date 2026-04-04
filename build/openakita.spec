# -*- mode: python ; coding: utf-8 -*-
"""
OpenAkita PyInstaller spec file

Usage:
  pyinstaller build/openakita.spec
"""

import os
import sys
import shutil
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# Project root directory
PROJECT_ROOT = Path(SPECPATH).parent
SRC_DIR = PROJECT_ROOT / "src"

# Force clean output directories to avoid macOS symlink conflicts
_dist_server = PROJECT_ROOT / "dist" / "openakita-server"
if _dist_server.exists():
    print(f"[spec] Removing existing output: {_dist_server}")
    shutil.rmtree(_dist_server)

# ============== Hidden Imports ==============
# Dynamic imports that PyInstaller static analysis may miss

hidden_imports_core = [
    # stdlib dunder module required by pip (e.g. `from __future__ import annotations`)
    "__future__",
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
    "openakita.channels.registry",
    "openakita.channels.group_response",
    "openakita.channels.policy",
    "openakita.channels.bot_config",
    "openakita.channels.chat_aliases",
    "openakita.channels.retry",
    "openakita.channels.text_splitter",
    "openakita.channels.media_parser",
    "openakita.channels.adapters",
    "openakita.channels.adapters.telegram",
    "openakita.channels.adapters.feishu",
    "openakita.channels.adapters.dingtalk",
    "openakita.channels.adapters.onebot",
    "openakita.channels.adapters.qq_official",
    "openakita.channels.adapters.wework_bot",
    "openakita.channels.adapters.wework_ws",
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
    "openakita.setup.qqbot_onboard",
    "openakita.setup.wecom_onboard",
    "openakita.setup.feishu_onboard",
    "openakita.mcp_servers",
    "openakita.mcp_servers.desktop_control",
    "openakita.mcp_servers.web_search",
    "openakita.orchestration",
    "openakita.orchestration.bus",
    "openakita.tracing",
    "openakita.logging",
    "openakita.tools",
    "openakita.tools.shell",
    "openakita.tools._import_helper",
    # -- Hub & Store (Agent Store / Skill Store 平台集成) --
    "openakita.hub",
    "openakita.hub.agent_hub_client",
    "openakita.hub.skill_store_client",
    "openakita.agents.packager",
    "openakita.agents.presets",
    "openakita.agents.profile",
    # -- tools.handlers / definitions (新增模块需显式声明，避免缓存遗漏) --
    "openakita.tools.handlers.agent_hub",
    "openakita.tools.handlers.skill_store",
    "openakita.tools.handlers.agent",
    "openakita.tools.handlers.agent_package",
    "openakita.tools.handlers.config",
    "openakita.tools.definitions.agent_hub",
    "openakita.tools.definitions.skill_store",
    "openakita.tools.definitions.agent",
    "openakita.tools.definitions.agent_package",
    "openakita.tools.definitions.config",
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
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "fastapi",
    "fastapi.middleware",           # CORSMiddleware 等中间件 (server.py 直接导入)
    "fastapi.middleware.cors",
    "starlette",                    # fastapi 底层框架 (运行时必需)
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.responses",
    "starlette.websockets",
    "pydantic",
    "pydantic.deprecated",          # pydantic v2 兼容层 (运行时动态加载)
    "pydantic._internal",
    "pydantic._internal._config",
    "pydantic_settings",
    "anthropic",
    "anthropic.types",              # brain.py 直接导入 Message/TextBlock/ToolUseBlock
    "openai",
    "httpx",
    "httpx._transports",            # 传输层 (动态选择 default/asyncio)
    "httpx._transports.default",
    "aiofiles",
    "aiofiles.os",                  # file.py 直接导入 (stat/remove 等操作)
    "aiosqlite",
    "yaml",
    "dotenv",
    "tenacity",
    "typer",
    "typer.core",                   # typer 内部核心
    "click",                        # typer 依赖 (PyInstaller 可能无法自动追踪)
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
    "lxml._elementpath",        # lxml XPath 引擎 (C 扩展，PyInstaller 常遗漏)
    "fake_useragent",           # ddgs 随机 User-Agent
    "fake_useragent.data",      # fake_useragent 数据文件 (browsers.jsonl, importlib.resources 动态加载)
    "h2",                       # ddgs HTTP/2 支持
    "hpack",                    # h2 依赖: HTTP/2 头部压缩
    "hyperframe",               # h2 依赖: HTTP/2 帧协议
    "httpcore",                 # httpx 传输层
    "socksio",                  # httpx SOCKS 代理支持 (系统代理工具常用 socks5://)
    "certifi",                  # SSL CA bundle (httpx/urllib3 依赖)
    "psutil",                   # Process info (~1MB)
    "pyperclip",                # Clipboard (~50KB)
    "websockets",               # WebSocket protocol (~500KB)
    "aiohttp",                  # Async HTTP server (~2MB, used by wework/qq webhook)
    "aiohttp.web",
    "aiohttp._http_parser",     # aiohttp C 扩展 (HTTP 解析加速，PyInstaller 常遗漏)
    "aiohttp._helpers",         # aiohttp C 扩展 (辅助函数)
    "multidict",                # aiohttp 依赖: 多值字典
    "yarl",                     # aiohttp 依赖: URL 解析
    "frozenlist",               # aiohttp 依赖: 不可变列表
    "aiosignal",                # aiohttp 依赖: 异步信号
    # (Python stdlib 模块通过下方 _collect_stdlib_modules() 自动收集，无需在此手动列举)
    # -- MCP (Model Context Protocol) --
    "mcp.server.fastmcp",       # FastMCP 服务端 (web_search MCP server)
    "mcp.client.stdio",         # MCP stdio 客户端
    "mcp.client.streamable_http",  # MCP HTTP 客户端
    # -- Document processing (skill dependencies, bundled directly) --
    "docx",                     # python-docx: Word files (~1MB)
    "docx.opc",                 # python-docx 包格式
    "docx.oxml",                # python-docx XML 层
    "openpyxl",                 # Excel files (~5MB)
    "openpyxl.workbook",        # openpyxl 工作簿
    "openpyxl.worksheet",       # openpyxl 工作表
    "openpyxl.cell._writer",    # openpyxl C 扩展 (单元格写入加速，PyInstaller 常遗漏)
    "pptx",                     # python-pptx: PowerPoint files (~3MB)
    "pptx.opc",                 # python-pptx 包格式
    "pptx.oxml",                # python-pptx XML 层
    "fitz",                     # PyMuPDF: PDF files (~15MB)
    "pypdf",                    # pypdf: PDF fallback (~2MB)
    # -- Image processing --
    "PIL",                      # Pillow: image format conversion (~10MB)
    # -- Desktop automation (cross-platform parts) --
    "mss",                      # Screenshot capture (~1MB)
    "mss.tools",
    # -- IM channel adapters (small, bundled to avoid install-on-config bugs) --
    "telegram",                 # python-telegram-bot: 核心 IM 通道 (~5MB)
    "telegram.ext",             # telegram 扩展框架 (Application/Handler)
    "telegram.ext.filters",     # 消息过滤器 (MessageHandler 需要)
    "telegram.request",         # HTTPXRequest (自定义超时配置)
    "telegram.constants",       # Telegram API 常量
    "telegram.error",           # Telegram 异常类
    # lark_oapi: 10K+ 自动生成的 API 文件，hidden_imports 无法完整收集
    # (wildcard imports: from .api import * 等)。改为在 datas 中直接复制整包目录，
    # 确保飞书 IM 通道开箱即用，不再依赖运行时自动安装。
    "requests",                 # lark_oapi 依赖: HTTP 客户端
    "requests.adapters",
    "requests.auth",
    "requests.cookies",
    "requests.exceptions",
    "requests.models",
    "requests.sessions",
    "requests.structures",
    "requests.utils",
    "requests_toolbelt",        # lark_oapi 依赖: multipart 上传等
    "urllib3",                  # requests 依赖: HTTP 连接池 (可能已由 httpx 间接引入)
    "charset_normalizer",       # requests 依赖: 编码检测
    "dingtalk_stream",          # DingTalk Stream (~2MB)
    "Crypto",                   # pycryptodome for WeWork (~3MB)
    "Crypto.Cipher",
    "Crypto.Cipher.AES",
    "Crypto.Util",              # pycryptodome 工具模块 (AES 运行时依赖)
    "Crypto.Util.Padding",      # 加解密填充 (AES CBC 模式常用)
    "botpy",                    # QQ Bot (~5MB)
    "botpy.message",            # QQ Bot 消息模块
    "pilk",                     # SILK 语音编解码 (QQ 语音格式, audio_utils.py 使用)
    "nacl",                     # PyNaCl: ed25519 签名验证 (QQ 官方机器人)
    "nacl.signing",             # 签名验证
    "nacl.exceptions",          # 签名异常
    "cryptography",             # cryptography: AES-256-CBC 文件解密 (wework_ws)
    "cryptography.hazmat",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.ciphers",
    # -- 浏览器自动化 (原为外置模块，现直接打包以提高用户体验) --
    "playwright",               # Playwright 浏览器自动化 (~20MB Python 包)
    "playwright.async_api",
    "playwright._impl",
    "browser_use",              # browser-use AI 代理 (~5MB)
    "browser_use.agent",
    "browser_use.agent.prompts",
    "browser_use.agent.system_prompts",  # importlib.resources.files() 需要此包可导入
    "browser_use.code_use",
    "langchain_openai",         # LangChain OpenAI adapter (~3MB)
    "langchain_openai.chat_models",
    "langchain_openai.chat_models.base",
    "langchain_core",           # LangChain 核心 (browser-use 依赖)
    "langchain_core.language_models",
    "langchain_core.language_models.chat_models",
    "langchain_core.language_models.base",
    "langchain_core.messages",
    "langchain_core.messages.ai",
    "langchain_core.messages.human",
    "langchain_core.messages.system",
    "langchain_core.messages.tool",
    "langchain_core.messages.utils",
    "langchain_core.callbacks",
    "langchain_core.callbacks.manager",
    "langchain_core.callbacks.base",
    "langchain_core.callbacks.streaming_stdout",
    "langchain_core.outputs",
    "langchain_core.outputs.chat_result",
    "langchain_core.outputs.chat_generation",
    "langchain_core.outputs.generation",
    "langchain_core.outputs.llm_result",
    "langchain_core.utils",
    "langchain_core.utils.function_calling",
    "langchain_core.utils.pydantic",
    "langchain_core.utils.utils",
    "langchain_core.runnables",
    "langchain_core.runnables.base",
    "langchain_core.runnables.config",
    "langchain_core.load",
    "langchain_core.load.serializable",
    "langchain_core.load.load",
    "langchain_core.prompts",
    "langchain_core.tools",
    "langsmith",                # LangChain 依赖
    "langsmith.run_helpers",
    # -- browser-use 运行时传递依赖 --
    "pyee",                     # playwright 依赖: EventEmitter
    "greenlet",                 # playwright 依赖: 协程桥接
    "tiktoken",                 # langchain-openai 依赖: token 计数
    "tiktoken_ext",             # tiktoken 动态注册 (importlib 加载，PyInstaller 无法静态追踪)
    "tiktoken_ext.openai_public",
    "bubus",                    # browser-use 事件总线
    "cdp_use",                  # browser-use CDP 协议支持
    "browser_use_sdk",          # browser-use SDK 客户端
    "posthog",                  # browser-use 遥测 (运行时加载)
    "screeninfo",               # browser-use 屏幕信息检测
    "pyotp",                    # browser-use OTP 支持
    "markdownify",              # browser-use HTML→Markdown 转换
    "beautifulsoup4",           # markdownify 依赖
    "bs4",                      # beautifulsoup4 实际导入名
    "portalocker",              # bubus 依赖: 文件锁
    "uuid7",                    # bubus 依赖: UUID v7
    "uuid_extensions",          # uuid7 运行时依赖
    *collect_submodules("simplejson"),  # browser-use JSON 序列化 (需要完整收集子模块，否则 requests 会因缺少 simplejson.errors 而崩溃)
    "cloudpickle",              # browser-use 序列化
    "backoff",                  # posthog 依赖: 重试
    "monotonic",                # posthog 依赖: 单调时钟
    "distro",                   # posthog 依赖: Linux 发行版检测
]

# ============== Platform-specific hidden imports ==============
if sys.platform == "win32":
    hidden_imports_core += [
        "pyautogui",
        "pyscreeze",
        "pytweening",
        "pywinauto",
        "pywinauto.controls",
        "pywinauto.controls.uiawrapper",
        "pywinauto.findwindows",
        "pywinauto.timings",
        "pywinauto.uia_element_info",
        "pywinauto.backend",
        "comtypes",
        "comtypes.client",
        "comtypes.gen",
    ]

# ============== Auto-collect Python stdlib ==============
# PyInstaller 默认只打包主程序引用到的标准库，但运行时可能需要其他标准库模块
# （如 timeit/lzma 等）。自动收集全部标准库模块，一劳永逸消除此类问题。
# 额外包体积约 5-10MB。

def _collect_stdlib_modules():
    """收集 Python 全部标准库顶层模块名（纯 Python + C 扩展）"""
    import pkgutil

    # 跳过：测试框架、IDE 工具、GUI 框架、打包工具等不需要的模块
    _SKIP = {
        "test", "tests", "idlelib", "tkinter", "turtledemo", "turtle",
        "lib2to3", "ensurepip", "venv", "distutils", "pydoc_data",
        "pydoc", "antigravity", "this",
    }
    # Keep dunder stdlib modules like __future__; they are required by pip
    # and some runtime code paths on Windows bundled interpreter checks.
    _SKIP_PREFIXES = ("_pyrepl",)

    stdlib_names = set()

    # 方式 1: sys.stdlib_module_names (Python 3.10+)，包含全部标准库（含 C 扩展）
    if hasattr(sys, "stdlib_module_names"):
        for name in sys.stdlib_module_names:
            if name in _SKIP or any(name.startswith(p) for p in _SKIP_PREFIXES):
                continue
            stdlib_names.add(name)

    # 方式 2: 遍历 Lib 目录，捕获 sys.stdlib_module_names 可能遗漏的包
    stdlib_path = os.path.dirname(os.__file__)
    for importer, modname, ispkg in pkgutil.iter_modules([stdlib_path]):
        if modname in _SKIP or any(modname.startswith(p) for p in _SKIP_PREFIXES):
            continue
        stdlib_names.add(modname)

    return sorted(stdlib_names)

_stdlib_modules = _collect_stdlib_modules()
print(f"[spec] Auto-collected {len(_stdlib_modules)} stdlib modules")

hidden_imports = hidden_imports_core + _stdlib_modules

# Collect all OpenAkita LLM submodules to prevent regressions when adding
# new files (e.g. openakita.llm.cache/retry/sse) that may be imported
# transitively but missed by static analysis in frozen builds.
hidden_imports += collect_submodules("openakita.llm")

# ============== Excludes ==============

excludes_core = [
    # 已从项目中移除的重型模块（防止被间接拉入）
    "sentence_transformers",
    "chromadb",
    "torch",
    "torchvision",
    "torchaudio",
    "zmq",
    "pyzmq",
    "whisper",
    # browser-use 的 provider SDK (lazy import，只用 langchain_openai，其他排除)
    "google_genai",
    "google.genai",
    "google.api_core",
    "google.auth",
    "google_auth_oauthlib",
    "google_api_core",
    "google_api_python_client",
    "googleapiclient",
    "groq",
    "ollama",
    "reportlab",
    "authlib",
    "inquirerpy",
    "langchain",
    # Heavy packages not needed (often pulled in from global site-packages)
    "cv2",
    "opencv_python",
    "matplotlib",
    "scipy",
    "pandas",
    "psycopg2",
    "psycopg2_binary",
    # GUI toolkits (not needed for headless server)
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    # Test frameworks
    "test",
    "tests",
    "pytest",
    "_pytest",
]

excludes = excludes_core

# ============== Data Files ==============
# Non-Python files to be bundled

datas = []

# certifi CA bundle: httpx/urllib3/requests all rely on certifi to find cacert.pem.
# PyInstaller's built-in hook may not always collect it correctly, causing
# FileNotFoundError: [Errno 2] No such file or directory on ALL HTTPS requests
# (and even HTTP, since httpx creates SSL context at AsyncClient.__init__ time).
try:
    import certifi
    _certifi_pem = certifi.where()
    _certifi_dir = str(Path(_certifi_pem).parent)
    datas.append((_certifi_dir, "certifi"))
    print(f"[spec] Bundling certifi CA bundle: {_certifi_pem}")
except ImportError:
    print("[spec] WARNING: certifi not installed, CA bundle not bundled")

# rich._unicode_data: filename contains hyphen (unicode17-0-0.py), PyInstaller cannot
# handle via hidden_imports, must be copied as data file
import rich._unicode_data as _rud
_rud_dir = str(Path(_rud.__file__).parent)
datas.append((_rud_dir, "rich/_unicode_data"))

# fake_useragent 数据文件 (browsers.jsonl)
# fake_useragent 使用 importlib.resources.files("fake_useragent.data") 动态加载数据文件。
# importlib.resources 要求 fake_useragent.data 是可导入的包，但该目录缺少 __init__.py
# （隐式命名空间包在 PyInstaller 中不工作）。
# 解决：① 将数据文件打包到 fake_useragent/data/ ② 创建临时 __init__.py 一起打包
try:
    import fake_useragent as _fua
    _fua_data_dir = Path(_fua.__file__).parent / "data"
    if _fua_data_dir.exists():
        datas.append((str(_fua_data_dir), "fake_useragent/data"))
        # 确保 data 目录有 __init__.py，使 importlib.resources 能将其作为包导入
        _fua_init = _fua_data_dir / "__init__.py"
        if not _fua_init.exists():
            import tempfile as _tmpmod
            _tmp_init = Path(_tmpmod.gettempdir()) / "fake_useragent_data_init.py"
            _tmp_init.write_text("# auto-generated for PyInstaller\n", encoding="utf-8")
            datas.append((str(_tmp_init), "fake_useragent/data"))
            print(f"[spec] Created temporary __init__.py for fake_useragent.data")
        print(f"[spec] Bundling fake_useragent data: {_fua_data_dir}")
except ImportError:
    print("[spec] WARNING: fake_useragent not installed, data files not bundled")

# browser_use 数据文件 (system prompt .md 模板)
# browser_use 使用 importlib.resources.files('browser_use.agent.system_prompts') 加载 .md 模板，
# PyInstaller 默认只打包 .py，必须显式将 .md 文件作为 data 打包。
try:
    import browser_use as _bu
    _bu_pkg_dir = Path(_bu.__file__).parent
    # agent/system_prompts/*.md
    _bu_prompts_dir = _bu_pkg_dir / "agent" / "system_prompts"
    if _bu_prompts_dir.exists():
        datas.append((str(_bu_prompts_dir), "browser_use/agent/system_prompts"))
        print(f"[spec] Bundling browser_use prompt templates: {_bu_prompts_dir}")
    # code_use/system_prompt.md
    _bu_code_prompt = _bu_pkg_dir / "code_use" / "system_prompt.md"
    if _bu_code_prompt.exists():
        datas.append((str(_bu_code_prompt), "browser_use/code_use"))
        print(f"[spec] Bundling browser_use code_use prompt: {_bu_code_prompt}")
except ImportError:
    print("[spec] WARNING: browser_use not installed, prompt templates not bundled")

# Web frontend for remote browser access (built by: cd apps/setup-center && npm run build:web)
# Bundled to openakita/web/ so _find_web_dist() in server.py can locate it
# via Path(__file__).parent.parent / "web" → _internal/openakita/web/
web_dist_dir = PROJECT_ROOT / "apps" / "setup-center" / "dist-web"
if (web_dist_dir / "index.html").exists():
    datas.append((str(web_dist_dir), "openakita/web"))
    print(f"[spec] Bundling web frontend: {web_dist_dir}")
else:
    print("[spec] INFO: dist-web not found, web remote access will not be available")

# openakita source code: needed by bridge subcommand which runs via the bundled
# system python.exe (not the frozen executable), so it needs importable .py files
# in _internal/openakita/
_openakita_src = SRC_DIR / "openakita"
if _openakita_src.exists():
    datas.append((str(_openakita_src), "openakita"))
    print(f"[spec] Bundling openakita source: {_openakita_src}")

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
    import subprocess as _sp
    with open(_pyproject_path, "rb") as _f:
        _pyproject_version = tomllib.load(_f)["project"]["version"]
    # Capture git short hash at build time
    _git_hash = "unknown"
    try:
        _git_hash = _sp.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short=7", "HEAD"],
            stderr=_sp.DEVNULL, text=True
        ).strip()
    except Exception:
        pass
    # Write version+hash to build dir (not source tree) so local builds don't dirty git
    _version_file = PROJECT_ROOT / "build" / "_bundled_version.txt"
    _version_file.write_text(f"{_pyproject_version}+{_git_hash}", encoding="utf-8")
    datas.append((str(_version_file), "openakita"))

# lark_oapi (飞书 SDK): 10K+ 自动生成的 API 文件，PyInstaller hidden_imports 无法
# 完整收集 (wildcard imports: from .api import *)。直接复制整包目录作为 data files，
# 确保飞书 IM 通道开箱即用，不再依赖运行时自动安装 (运行时安装需要独立 Python
# 解释器，在打包环境中不可靠)。
try:
    import lark_oapi as _lark
    _lark_dir = str(Path(_lark.__file__).parent)
    datas.append((_lark_dir, "lark_oapi"))
    print(f"[spec] Bundling lark_oapi: {_lark_dir}")
except ImportError:
    print("[spec] WARNING: lark_oapi not installed, feishu channel will need runtime install")

# requests_toolbelt (lark_oapi 依赖): 同样直接复制，避免 PyInstaller 遗漏
try:
    import requests_toolbelt as _rt
    _rt_dir = str(Path(_rt.__file__).parent)
    datas.append((_rt_dir, "requests_toolbelt"))
    print(f"[spec] Bundling requests_toolbelt: {_rt_dir}")
except ImportError:
    print("[spec] WARNING: requests_toolbelt not installed")

# httpx 及其传递依赖: bridge.py 子进程通过独立 python.exe 运行（非冻结可执行文件），
# 只能从 sys.path 上的 loose 文件导入，无法读取 PYZ 归档。
# 飞书/企微/QQ 扫码 onboard 以及 list-models、health-check 等命令均依赖 httpx。
_bridge_subprocess_pkgs = [
    ("httpx", "httpx"),           # HTTP 客户端
    ("httpcore", "httpcore"),     # httpx 核心传输层
    ("h11", "h11"),              # HTTP/1.1 协议解析 (httpcore 依赖)
    ("anyio", "anyio"),          # 异步 I/O (httpx AsyncClient 依赖)
    ("sniffio", "sniffio"),      # 异步库检测 (anyio 依赖)
    ("idna", "idna"),            # 国际域名处理 (httpx URL 解析依赖)
    ("socksio", "socksio"),      # SOCKS 代理支持 (httpx[socks])
]
for _imp_name, _pkg_name in _bridge_subprocess_pkgs:
    try:
        _mod = __import__(_imp_name)
        _mod_dir = str(Path(_mod.__file__).parent)
        datas.append((_mod_dir, _pkg_name))
        print(f"[spec] Bundling {_pkg_name}: {_mod_dir}")
    except ImportError:
        print(f"[spec] WARNING: {_imp_name} not installed, bridge subprocess may fail")

# Built-in Python interpreter + pip (bundled mode can install optional modules without host Python)
# IMPORTANT: do NOT bundle a venv launcher (it may require pyvenv.cfg at runtime).
# Prefer base interpreter from sys.base_prefix, fallback to sys.executable.
_sys_python_exe = Path(sys.executable)
_base_prefix = Path(getattr(sys, "base_prefix", "")) if getattr(sys, "base_prefix", "") else None
if _base_prefix:
    if sys.platform == "win32":
        _base_candidates = [_base_prefix / "python.exe"]
    else:
        _base_candidates = [_base_prefix / "bin" / "python3", _base_prefix / "bin" / "python"]
    for _cand in _base_candidates:
        if _cand.exists():
            _sys_python_exe = _cand
            print(f"[spec] Using base interpreter for bundled python: {_sys_python_exe}")
            break
if _sys_python_exe.exists():
    datas.append((str(_sys_python_exe), "."))  # python* -> _internal/
    # macOS python launcher (`bin/python3`) resolves to ../Resources/Python.app.
    # Bundle Python.app at _internal/Resources/Python.app to preserve this
    # relative lookup after relocation.
    if sys.platform == "darwin" and _base_prefix:
        _py_app_dir = _base_prefix / "Resources" / "Python.app"
        if _py_app_dir.exists():
            _rel_py_app_dst = "Resources/Python.app"
            datas.append((str(_py_app_dir), _rel_py_app_dst))
            print(f"[spec] Bundling macOS Python.app: {_py_app_dir} -> {_rel_py_app_dst}")
        else:
            print(f"[spec] WARNING: macOS Python.app not found at {_py_app_dir}")

# pip and its dependencies (minimal set needed for pip install)
import pip
_pip_dir = str(Path(pip.__file__).parent)
datas.append((_pip_dir, "pip"))

# pip vendor dependencies (pip._vendor contains requests, urllib3 etc.)
# Already included in pip directory, no extra handling needed

# Playwright driver (node.js executable + browser protocol implementation)
# playwright._impl._driver 在运行时通过 subprocess 启动 node 进程，
# 必须将 driver 目录打包，否则 "playwright install" 可以完成但运行时找不到 driver。
try:
    import playwright
    _pw_pkg_dir = Path(playwright.__file__).parent
    _pw_driver_dir = _pw_pkg_dir / "driver"
    if _pw_driver_dir.exists():
        datas.append((str(_pw_driver_dir), "playwright/driver"))
        print(f"[spec] Bundling Playwright driver: {_pw_driver_dir}")
    else:
        print(f"[spec] WARNING: Playwright driver dir not found: {_pw_driver_dir}")
except ImportError:
    print("[spec] WARNING: playwright not installed, driver not bundled")

# Playwright Chromium browser binary (bundled to avoid user needing 'playwright install chromium')
# 构建时需预先运行: playwright install chromium
# Chromium 默认位于 PLAYWRIGHT_BROWSERS_PATH 或 playwright 包内的 .local-browsers
try:
    _pw_browsers_bundled = False
    # 优先检查 playwright 包内的浏览器（playwright install --with-deps 后的位置）
    _pw_local_browsers = _pw_pkg_dir / ".local-browsers"
    if _pw_local_browsers.exists():
        datas.append((str(_pw_local_browsers), "playwright/.local-browsers"))
        _pw_browsers_bundled = True
        print(f"[spec] Bundling Playwright local browsers: {_pw_local_browsers}")
    else:
        # 检查默认浏览器安装路径
        import subprocess as _sp2
        try:
            _pw_browser_path = _sp2.check_output(
                [sys.executable, "-c",
                 "from playwright._impl._driver import compute_driver_executable; "
                 "import os; print(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', ''))"],
                text=True, stderr=_sp2.DEVNULL
            ).strip()
        except Exception:
            _pw_browser_path = ""

        if not _pw_browser_path:
            # 使用 playwright 默认路径
            if sys.platform == "win32":
                _pw_browser_path = str(Path.home() / "AppData" / "Local" / "ms-playwright")
            elif sys.platform == "darwin":
                _pw_browser_path = str(Path.home() / "Library" / "Caches" / "ms-playwright")
            else:
                _pw_browser_path = str(Path.home() / ".cache" / "ms-playwright")

        _pw_browser_dir = Path(_pw_browser_path)
        if _pw_browser_dir.exists():
            # 只打包 chromium 目录（不打包其他浏览器）
            for _chromium_dir in _pw_browser_dir.iterdir():
                if _chromium_dir.is_dir() and "chromium" in _chromium_dir.name.lower():
                    datas.append((str(_chromium_dir), f"playwright-browsers/{_chromium_dir.name}"))
                    _pw_browsers_bundled = True
                    print(f"[spec] Bundling Playwright Chromium: {_chromium_dir}")
                    break

    if not _pw_browsers_bundled:
        print("[spec] WARNING: Playwright Chromium not found. Run 'playwright install chromium' before building.")
except Exception as _pw_err:
    print(f"[spec] WARNING: Failed to detect Playwright browsers: {_pw_err}")

# Built-in MCP server configs (chrome-browser, desktop-control, web-search, etc.)
mcps_dir = PROJECT_ROOT / "mcps"
if mcps_dir.exists():
    datas.append((str(mcps_dir), "openakita/builtin_mcps"))
    print(f"[spec] Bundling built-in MCP configs: {mcps_dir}")

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

# Contract A: all platforms use onedir output so runtime can always
# discover bundled interpreter at openakita-server/_internal/python*
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="openakita-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=(sys.platform != "darwin"),
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
    upx=(sys.platform != "darwin"),
    upx_exclude=[],
    name="openakita-server",
)
