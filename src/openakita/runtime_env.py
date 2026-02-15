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
    """将 ~/.openakita/modules/*/site-packages 注入 sys.path（兜底机制）。

    在 PyInstaller 打包环境中，Rust 端通过 PYTHONPATH 环境变量注入模块路径，
    但在 CLI 直接运行或 PYTHONPATH 未设置时，此函数作为兜底确保已安装的
    可选模块能被正确发现。
    """
    if not IS_FROZEN:
        return
    modules_base = _get_openakita_root() / "modules"
    if not modules_base.exists():
        return
    injected = []
    for module_dir in modules_base.iterdir():
        if not module_dir.is_dir():
            continue
        sp = module_dir / "site-packages"
        if sp.is_dir() and str(sp) not in sys.path:
            sys.path.insert(0, str(sp))
            injected.append(module_dir.name)
    if injected:
        logger.debug(f"已注入模块路径: {', '.join(injected)}")
