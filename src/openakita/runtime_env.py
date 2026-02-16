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


def _get_openakita_root() -> Path:
    """获取 ~/.openakita 根目录路径 (避免循环导入 config)"""
    return Path.home() / ".openakita"


def get_python_executable() -> str | None:
    """获取可用的 Python 解释器路径。

    PyInstaller 环境下: 查找外置 Python (venv > embedded > PATH)
    常规环境下: 返回 sys.executable
    """
    if not IS_FROZEN:
        return sys.executable

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

    # 3. PATH 中的 python
    py_path = shutil.which("python3") or shutil.which("python")
    if py_path:
        logger.debug(f"使用 PATH Python: {py_path}")
    else:
        logger.warning("未找到可用的 Python 解释器")
    return py_path


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
