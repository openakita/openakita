"""
运行时环境检测 - 兼容 PyInstaller 打包和常规 Python 环境

PyInstaller 打包后 sys.executable 指向 openakita-server.exe 而非 Python 解释器，
本模块提供统一的运行时环境检测层，确保 pip install / 脚本执行等功能正常工作。
"""

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

IS_FROZEN = getattr(sys, "frozen", False)
"""是否在 PyInstaller 打包环境中运行"""


def _find_python_in_dir(directory: Path) -> Path | None:
    """在给定目录中查找 Python 可执行文件"""
    if sys.platform == "win32":
        candidates = ["python.exe", "python3.exe"]
    else:
        candidates = ["python3", "python"]

    for name in candidates:
        py = directory / name
        if py.exists():
            return py
    # 也检查 bin/ 或 Scripts/ 子目录
    for sub in ("bin", "Scripts"):
        sub_dir = directory / sub
        if sub_dir.is_dir():
            for name in candidates:
                py = sub_dir / name
                if py.exists():
                    return py
    return None


def _is_windows_store_stub(path: str) -> bool:
    """检查是否为 Windows Store 的假 Python 桩（App Execution Alias）。
    这些桩位于 WindowsApps 目录，执行时返回 9009 而不是真正运行 Python。"""
    return "WindowsApps" in path or "AppInstallerPythonRedirector" in path


def _which_real_python() -> str | None:
    """在 PATH 中查找真正可用的 Python，跳过 Windows Store 桩。"""
    if sys.platform == "win32":
        # Windows: python.exe 优先（python3.exe 通常只有 Store 桩提供）
        candidates = ["python", "python3"]
    else:
        candidates = ["python3", "python"]

    for name in candidates:
        path = shutil.which(name)
        if path and not _is_windows_store_stub(path):
            return path
    return None


def _scan_common_python_dirs() -> str | None:
    """扫描各平台常见 Python 安装目录（PATH 失效时的兜底）。"""
    import glob

    if sys.platform == "win32":
        patterns = [
            r"C:\Python3*\python.exe",
            r"C:\Program Files\Python3*\python.exe",
            r"C:\Program Files (x86)\Python3*\python.exe",
        ]
        for pattern in patterns:
            matches = sorted(glob.glob(pattern), reverse=True)
            if matches:
                return matches[0]
        # 用户级安装 (AppData\Local\Programs\Python)
        local_programs = Path.home() / "AppData" / "Local" / "Programs" / "Python"
        if local_programs.exists():
            for py_dir in sorted(local_programs.iterdir(), reverse=True):
                py = py_dir / "python.exe"
                if py.exists():
                    return str(py)
    elif sys.platform == "darwin":
        # macOS: Homebrew, python.org installer, Xcode CLI tools
        for pattern in [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.*/bin/python3",
            "/usr/bin/python3",
        ]:
            matches = sorted(glob.glob(pattern), reverse=True)
            if matches:
                return matches[0]
    else:
        # Linux: system, deadsnakes PPA, pyenv, user-local
        for pattern in [
            "/usr/bin/python3",
            "/usr/bin/python3.*",
            "/usr/local/bin/python3",
            str(Path.home() / ".pyenv/shims/python3"),
            str(Path.home() / ".local/bin/python3"),
        ]:
            matches = sorted(glob.glob(pattern), reverse=True)
            if matches:
                return matches[0]
    return None


def _get_python_from_env_var() -> str | None:
    """从环境变量 PYTHON / PYTHON3 / OPENAKITA_PYTHON 获取 Python 路径。
    用户可以通过设置环境变量来显式指定 Python 解释器。"""
    import os
    for var in ("OPENAKITA_PYTHON", "PYTHON3", "PYTHON"):
        val = os.environ.get(var)
        if val and Path(val).is_file():
            return val
    return None


def _get_python_from_configured_venv() -> str | None:
    """从 PYTHON_VENV_PATH 环境变量（由 .env 配置注入）获取虚拟环境中的 Python。
    Setup Center 在用户选择/创建 venv 后会将路径写入工作区 .env 中。"""
    import os
    venv_path = os.environ.get("PYTHON_VENV_PATH", "").strip()
    if not venv_path:
        return None
    venv_dir = Path(venv_path).expanduser()
    if not venv_dir.is_dir():
        return None
    py = _find_python_in_dir(venv_dir)
    if py:
        logger.debug(f"使用配置的 venv Python (PYTHON_VENV_PATH): {py}")
        return str(py)
    return None


def get_configured_venv_path() -> str | None:
    """获取虚拟环境路径（供提示词构建等模块使用）。

    优先级: PYTHON_VENV_PATH 环境变量 > 从当前 Python 解释器路径推断。
    """
    import os

    venv_path = os.environ.get("PYTHON_VENV_PATH", "").strip()
    if venv_path:
        p = Path(venv_path).expanduser()
        if p.is_dir():
            return str(p)

    if not IS_FROZEN:
        if sys.prefix != sys.base_prefix:
            return sys.prefix
        return None

    py = get_python_executable()
    if not py:
        return None
    py_path = Path(py)
    # Scripts/python.exe -> venv root, or bin/python -> venv root
    if py_path.parent.name in ("Scripts", "bin"):
        venv_root = py_path.parent.parent
        pyvenv_cfg = venv_root / "pyvenv.cfg"
        if pyvenv_cfg.exists():
            return str(venv_root)
    return None


def _get_openakita_root() -> Path:
    """获取 ~/.openakita 根目录路径 (避免循环导入 config)"""
    return Path.home() / ".openakita"


def get_python_executable() -> str | None:
    """获取可用的 Python 解释器路径。

    PyInstaller 环境下: 查找外置 Python
      (configured venv > workspace venv > home venv > embedded > env var > PATH)
    常规环境下: 返回 sys.executable
    """
    if not IS_FROZEN:
        return sys.executable

    # 0a. 用户通过 Setup Center 配置的 venv 路径（PYTHON_VENV_PATH）— 最高优先级
    configured = _get_python_from_configured_venv()
    if configured:
        return configured

    # 0b. 检查 {project_root}/data/venv/ — 工作区虚拟环境（系统专用，与用户环境隔离）
    try:
        from .config import settings
        workspace_venv = settings.project_root / "data" / "venv"
        py = _find_python_in_dir(workspace_venv)
        if py:
            logger.debug(f"使用工作区 venv Python: {py}")
            return str(py)
    except Exception:
        pass

    root = _get_openakita_root()

    # 1. 检查 ~/.openakita/venv/
    if sys.platform == "win32":
        venv_python = root / "venv" / "Scripts" / "python.exe"
    else:
        venv_python = root / "venv" / "bin" / "python"
    if venv_python.exists():
        logger.debug(f"使用 venv Python: {venv_python}")
        return str(venv_python)

    # 2. 检查 embedded python (~/.openakita/runtime/python/)
    runtime_dir = root / "runtime" / "python"
    if runtime_dir.exists():
        for tag_dir in sorted(runtime_dir.iterdir(), reverse=True):
            if not tag_dir.is_dir():
                continue
            for asset_dir in tag_dir.iterdir():
                if not asset_dir.is_dir():
                    continue
                py = _find_python_in_dir(asset_dir)
                if py:
                    logger.debug(f"使用 embedded Python: {py}")
                    return str(py)

    # 3. 环境变量显式指定 (OPENAKITA_PYTHON / PYTHON3 / PYTHON)
    env_py = _get_python_from_env_var()
    if env_py:
        logger.info(f"使用环境变量指定的 Python: {env_py}")
        return env_py

    # 4. PATH 中的 python（跳过 Windows Store 假桩）
    py_path = _which_real_python()
    if py_path:
        logger.info(f"使用 PATH Python: {py_path}")
        return py_path

    # 5. 常见安装目录扫描（PATH 失效时的兜底，支持 Windows/macOS/Linux）
    py_path = _scan_common_python_dirs()
    if py_path:
        logger.info(f"使用扫描发现的 Python: {py_path}")
        return py_path

    logger.warning("未找到可用的 Python 解释器")
    return None


def can_pip_install() -> bool:
    """检查当前环境是否支持 pip install"""
    py = get_python_executable()
    if not py:
        return False
    # PyInstaller 打包环境需要外置 Python 才能 pip install
    if IS_FROZEN:
        return py != sys.executable
    return True


def get_pip_command(packages: list[str]) -> list[str] | None:
    """获取 pip install 命令列表。

    Returns:
        命令参数列表 (如 ["python", "-m", "pip", "install", "pkg"])，
        若不支持则返回 None。
    """
    py = get_python_executable()
    if not py:
        return None
    # PyInstaller 打包环境需要外置 Python 才能 pip install
    if IS_FROZEN and py == sys.executable:
        return None
    return [py, "-m", "pip", "install", *packages]


def ensure_ssl_certs() -> None:
    """确保 SSL 证书在 PyInstaller 环境下可用。

    httpx 默认 trust_env=True，优先读取 SSL_CERT_FILE 环境变量。
    Conda/Anaconda 安装后会在系统环境变量中设置 SSL_CERT_FILE 指向
    Conda 自己的 cacert.pem（如 Anaconda3/Library/ssl/cacert.pem），
    但在非 Conda 环境中该路径不存在，导致 httpx 创建 SSL 上下文时
    抛出 FileNotFoundError: [Errno 2] No such file or directory。

    此函数检测并修正 SSL_CERT_FILE，确保它指向一个实际存在的证书文件。
    """
    if not IS_FROZEN:
        return

    import os

    # 如果 SSL_CERT_FILE 已设置且文件确实存在，则无需干预
    existing = os.environ.get("SSL_CERT_FILE", "").strip()
    if existing and Path(existing).is_file():
        return

    if existing:
        logger.warning(
            f"SSL_CERT_FILE points to non-existent file: {existing} "
            f"(likely set by Conda/Anaconda). Overriding with bundled CA bundle."
        )

    # 方式 1: certifi 模块可用且路径有效
    try:
        import certifi
        pem_path = certifi.where()
        if Path(pem_path).is_file():
            os.environ["SSL_CERT_FILE"] = pem_path
            logger.info(f"SSL_CERT_FILE set from certifi: {pem_path}")
            return
    except ImportError:
        pass

    # 方式 2: 在 PyInstaller _internal/ 目录中查找
    internal_dir = Path(sys.executable).parent
    if internal_dir.name != "_internal":
        internal_dir = internal_dir / "_internal"

    for candidate in [
        internal_dir / "certifi" / "cacert.pem",
        internal_dir / "certifi" / "cert.pem",
    ]:
        if candidate.is_file():
            os.environ["SSL_CERT_FILE"] = str(candidate)
            logger.info(f"SSL_CERT_FILE set from bundled path: {candidate}")
            return

    # 方式 3: 清除无效的 SSL_CERT_FILE，让 httpx 回退到 certifi.where()
    if existing:
        del os.environ["SSL_CERT_FILE"]
        logger.warning(
            "Removed invalid SSL_CERT_FILE. httpx will fall back to certifi default."
        )
        return

    logger.warning(
        "SSL CA bundle not found in PyInstaller environment. "
        "HTTPS requests may fail with [Errno 2] No such file or directory."
    )


def inject_module_paths() -> None:
    """将可选模块的 site-packages 目录注入 sys.path。

    路径来源（按优先级）：
    1. OPENAKITA_MODULE_PATHS 环境变量 — Tauri 端通过此变量传递已安装模块路径
    2. 扫描 ~/.openakita/modules/*/site-packages — 兜底机制

    重要：必须使用 sys.path.append() 而非 insert(0)！
    PyInstaller 打包环境中，内置模块（如 pydantic）位于 _MEIPASS/_internal 目录
    且在 sys.path 前端。如果外部模块路径被插入到前面，外部的 pydantic 会覆盖
    内置版本，其 C 扩展 pydantic_core._pydantic_core 与 PyInstaller 环境不兼容，
    导致进程在 import 阶段直接崩溃。

    注意：Tauri 端不使用 PYTHONPATH 注入模块路径，因为 Python 启动时
    PYTHONPATH 会被自动插入到 sys.path 最前面，无法保证内置模块优先。
    """
    if not IS_FROZEN:
        return

    import os

    injected = []

    # 来源 1：从 OPENAKITA_MODULE_PATHS 环境变量读取（Tauri 端设置）
    env_paths = os.environ.get("OPENAKITA_MODULE_PATHS", "")
    if env_paths:
        sep = ";" if sys.platform == "win32" else ":"
        for p in env_paths.split(sep):
            p = p.strip()
            if p and p not in sys.path:
                sys.path.append(p)
                injected.append(Path(p).parent.name)

    # 来源 2：扫描 ~/.openakita/modules/*/site-packages（兜底）
    # 跳过已内置到 core 包的模块，避免外部旧版本与内置版本冲突
    _BUILTIN_MODULE_IDS = {"browser"}
    modules_base = _get_openakita_root() / "modules"
    if modules_base.exists():
        for module_dir in modules_base.iterdir():
            if not module_dir.is_dir():
                continue
            if module_dir.name in _BUILTIN_MODULE_IDS:
                continue
            sp = module_dir / "site-packages"
            if sp.is_dir() and str(sp) not in sys.path:
                sys.path.append(str(sp))
                injected.append(module_dir.name)

    if injected:
        logger.info(f"已注入模块路径（追加到 sys.path 末尾）: {', '.join(injected)}")

    # Windows 下为含有 C 扩展 DLL 的模块（如 torch）添加 DLL 搜索路径。
    # Python 3.8+ 在 Windows 上不再将 sys.path 用于 DLL 解析，必须通过
    # os.add_dll_directory() 显式注册，否则 torch._C 等 PYD 的依赖 DLL
    # （c10.dll, torch_cpu.dll 等）无法被找到，导致 ImportError: DLL load failed。
    if sys.platform == "win32":
        _register_dll_directories(os)


def _register_dll_directories(os_module) -> None:
    """在 Windows 上为 sys.path 中含有 C 扩展 DLL 的目录注册 DLL 搜索路径。

    扫描 sys.path 中的每个路径，检查是否存在已知的 DLL 子目录
    （如 torch/lib/），然后通过 os.add_dll_directory() 注册。
    同时将 DLL 路径追加到 PATH 环境变量作为兜底。
    """
    # 已知需要注册 DLL 目录的包及其 DLL 子路径
    _DLL_SUBDIRS = [
        ("torch", "lib"),          # PyTorch: c10.dll, torch_cpu.dll, libiomp5md.dll
        ("torch", "bin"),          # PyTorch 某些版本把 DLL 放在 bin/
    ]

    registered = []
    for p in list(sys.path):
        p_path = Path(p)
        if not p_path.is_dir():
            continue
        for pkg, sub in _DLL_SUBDIRS:
            dll_dir = p_path / pkg / sub
            if dll_dir.is_dir():
                dll_str = str(dll_dir)
                try:
                    os_module.add_dll_directory(dll_str)
                    registered.append(dll_str)
                except OSError as e:
                    logger.warning(f"添加 DLL 路径失败: {dll_dir} - {e}")
                # 兜底：将 DLL 目录追加到 PATH（某些旧版 Python 或特殊环境）
                current_path = os_module.environ.get("PATH", "")
                if dll_str not in current_path:
                    os_module.environ["PATH"] = dll_str + ";" + current_path

    if registered:
        logger.info(f"已注册 Windows DLL 搜索路径: {', '.join(registered)}")


def inject_module_paths_runtime() -> int:
    """运行时重新扫描并注入模块路径（不要求 IS_FROZEN）。

    用于模块安装后无需重启即可加载新模块。
    与 inject_module_paths() 不同，此函数不检查 IS_FROZEN，
    可在任何环境下调用。

    Returns:
        新注入的路径数量
    """
    import os

    injected = []

    # 扫描 ~/.openakita/modules/*/site-packages
    modules_base = _get_openakita_root() / "modules"
    if modules_base.exists():
        for module_dir in modules_base.iterdir():
            if not module_dir.is_dir():
                continue
            sp = module_dir / "site-packages"
            if sp.is_dir() and str(sp) not in sys.path:
                sys.path.append(str(sp))
                injected.append(module_dir.name)

    if injected:
        logger.info(f"[Runtime] 已注入模块路径: {', '.join(injected)}")

    # Windows DLL 目录
    if sys.platform == "win32":
        _register_dll_directories(os)

    return len(injected)
