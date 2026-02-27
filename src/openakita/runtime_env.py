"""
运行时环境检测 - 兼容 PyInstaller 打包和常规 Python 环境

PyInstaller 打包后 sys.executable 指向 openakita-server.exe 而非 Python 解释器，
本模块提供统一的运行时环境检测层，确保 pip install / 脚本执行等功能正常工作。
"""

import logging
import json
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
    """快速检查是否为 Windows Store 的重定向桩（App Execution Alias）。

    AppInstallerPythonRedirector 是微软用来引导用户安装 Python 的假桩，
    运行时返回 exit code 9009，不是真正的 Python。
    注意：WindowsApps 目录下也可能有真正的 Microsoft Store 安装的 Python，
    不能仅凭路径排除，必须通过 verify_python_executable() 进一步验证。
    """
    return "AppInstallerPythonRedirector" in path


def verify_python_executable(path: str) -> bool:
    """验证一个 Python 可执行文件是否真正可用。

    实际运行 ``python --version``，确认返回码为 0 且输出以 ``Python 3.`` 开头。
    可排除 Windows Store 假桩（exit 9009）、损坏的安装、以及非 Python 3 的旧版本。
    """
    import subprocess

    try:
        kwargs: dict = {"capture_output": True, "text": True, "timeout": 5}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run([path, "--version"], **kwargs)
        if result.returncode != 0:
            logger.debug("Python 验证失败 (exit %d): %s", result.returncode, path)
            return False
        output = (result.stdout + result.stderr).strip()
        if output.startswith("Python 3."):
            logger.debug("Python 验证通过: %s → %s", path, output)
            return True
        logger.debug("Python 版本不符 (需要 3.x): %s → %s", path, output)
        return False
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as exc:
        logger.debug("Python 验证异常: %s → %s", path, exc)
        return False



# NOTE: _which_real_python / _scan_common_python_dirs / _get_python_from_env_var
# 已移除 — 不再搜索用户系统中的 Python，只使用项目自带/自行安装的 Python。
# 这消除了因用户 Anaconda、Windows Store 假桩、版本不一致等导致的冲突。


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
    if not py:
        return None
    py_str = str(py)
    if verify_python_executable(py_str):
        logger.debug("使用配置的 venv Python (PYTHON_VENV_PATH): %s", py_str)
        return py_str
    logger.warning("配置的 venv Python 存在但验证失败: %s", py_str)
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


def _get_bundled_internal_python() -> str | None:
    """查找 PyInstaller 打包时捆绑在 _internal/ 目录中的 Python 解释器。

    构建时 openakita.spec 会将 sys.executable 和 pip 一起复制到 _internal/，
    因此该 Python 版本与构建环境完全一致，不会产生兼容性问题。
    """
    if not IS_FROZEN:
        return None
    exe_dir = Path(sys.executable).parent
    internal_dir = exe_dir if exe_dir.name == "_internal" else exe_dir / "_internal"
    if not internal_dir.is_dir():
        return None
    if sys.platform == "win32":
        candidates = ["python.exe", "python3.exe"]
    else:
        candidates = ["python3", "python"]
    for name in candidates:
        py = internal_dir / name
        if py.exists() and verify_python_executable(str(py)):
            logger.debug("使用打包内置 Python (_internal): %s", py)
            return str(py)
    return None


def get_python_executable() -> str | None:
    """获取可用的 Python 解释器路径。

    **只使用项目自带或项目自行安装的 Python，不使用用户系统 Python。**

    PyInstaller 环境下查找优先级:
      1. Setup Center 配置的 venv (PYTHON_VENV_PATH)
      2. 工作区 venv ({project_root}/data/venv/)
      3. 全局 venv (~/.openakita/venv/)
      4. 打包内置 Python (_internal/python.exe)
      5. 嵌入式 Python (~/.openakita/runtime/python/)

    常规开发环境下: 返回 sys.executable
    """
    if not IS_FROZEN:
        return sys.executable

    # 1. 用户通过 Setup Center 配置的 venv 路径（PYTHON_VENV_PATH）— 最高优先级
    configured = _get_python_from_configured_venv()
    if configured:
        return configured

    # 2. 检查 {project_root}/data/venv/ — 工作区虚拟环境
    try:
        from .config import settings
        workspace_venv = settings.project_root / "data" / "venv"
        py = _find_python_in_dir(workspace_venv)
        if py and verify_python_executable(str(py)):
            logger.debug(f"使用工作区 venv Python: {py}")
            return str(py)
        elif py:
            logger.warning(f"工作区 venv Python 存在但验证失败，跳过: {py}")
    except Exception:
        pass

    root = _get_openakita_root()

    # 3. 检查 ~/.openakita/venv/
    if sys.platform == "win32":
        venv_python = root / "venv" / "Scripts" / "python.exe"
    else:
        venv_python = root / "venv" / "bin" / "python"
    if venv_python.exists():
        if verify_python_executable(str(venv_python)):
            logger.debug(f"使用 venv Python: {venv_python}")
            return str(venv_python)
        else:
            logger.warning(f"全局 venv Python 验证失败，跳过: {venv_python}")

    # 4. 打包内置 Python（_internal/ 目录，构建时捆绑的同版本 Python + pip）
    bundled = _get_bundled_internal_python()
    if bundled:
        return bundled

    # 5. 检查 embedded python (~/.openakita/runtime/python/)
    runtime_dir = root / "runtime" / "python"
    if runtime_dir.exists():
        for tag_dir in sorted(runtime_dir.iterdir(), reverse=True):
            if not tag_dir.is_dir():
                continue
            for asset_dir in tag_dir.iterdir():
                if not asset_dir.is_dir():
                    continue
                py = _find_python_in_dir(asset_dir)
                if py and verify_python_executable(str(py)):
                    logger.debug(f"使用 embedded Python: {py}")
                    return str(py)

    logger.warning(
        "未找到项目自带的 Python 解释器。"
        "已搜索: 配置的 venv → 工作区 venv → ~/.openakita/venv → "
        "打包内置 Python → 嵌入式 Python。"
        "请前往「设置中心 → Python 环境」使用「一键修复」自动下载安装。"
    )
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


_DEFAULT_PIP_INDEX = "https://mirrors.aliyun.com/pypi/simple/"
_DEFAULT_PIP_TRUSTED_HOST = "mirrors.aliyun.com"


def get_pip_command(packages: list[str], *, index_url: str | None = None) -> list[str] | None:
    """获取 pip install 命令列表（默认使用国内镜像源）。

    Args:
        packages: 要安装的包名列表
        index_url: 自定义镜像源 URL，为 None 时使用阿里云镜像

    Returns:
        命令参数列表，若不支持则返回 None。
    """
    import os

    py = get_python_executable()
    if not py:
        return None
    if IS_FROZEN and py == sys.executable:
        return None

    effective_index = (
        os.environ.get("PIP_INDEX_URL", "").strip()
        or index_url
        or _DEFAULT_PIP_INDEX
    )
    trusted_host = effective_index.split("//")[1].split("/")[0] if "//" in effective_index else ""

    return [
        py, "-m", "pip", "install",
        "-i", effective_index,
        "--trusted-host", trusted_host,
        "--prefer-binary",
        *packages,
    ]


def get_channel_deps_dir() -> Path:
    """获取 IM 通道依赖的隔离安装目录。

    路径: ~/.openakita/modules/channel-deps/site-packages
    该目录会被 inject_module_paths() 自动扫描并注入到 sys.path。
    """
    return _get_openakita_root() / "modules" / "channel-deps" / "site-packages"


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


def _sanitize_sys_path() -> None:
    """检测并清理 sys.path 中可能由外部环境泄漏的路径（纵深防御）。

    即使 Tauri 端已在启动时清除了 PYTHONPATH 等有害环境变量，
    仍可能有路径通过其他途径被注入（如 .pth 文件、site-packages 钩子等）。
    此函数移除不属于项目自有路径的 site-packages 目录，
    防止用户 Anaconda、系统 Python 等环境中的包覆盖内置模块。
    """
    if not IS_FROZEN:
        return

    import os

    meipass = getattr(sys, "_MEIPASS", "")
    openakita_root = str(_get_openakita_root())

    suspicious = []
    for p in list(sys.path):
        if not p:
            continue
        # 允许: PyInstaller 内部路径
        if meipass and p.startswith(meipass):
            continue
        # 允许: 项目数据目录 (~/.openakita/)
        if p.startswith(openakita_root):
            continue
        # 允许: 当前工作目录 ('' 或 '.')
        if p in ("", "."):
            continue
        # 允许: 临时目录（部分运行时动态生成）
        tmp = os.environ.get("TEMP", os.environ.get("TMPDIR", ""))
        if tmp and p.startswith(tmp):
            continue
        # 检测: 含有 site-packages 的外部路径是危险信号
        p_lower = p.lower().replace("\\", "/")
        if "site-packages" in p_lower or "dist-packages" in p_lower:
            suspicious.append(p)

    if suspicious:
        for p in suspicious:
            sys.path.remove(p)
        logger.warning(
            f"已清理 {len(suspicious)} 个外部 site-packages 路径 "
            f"(可能来自用户 Anaconda/系统 Python): {suspicious[:5]}"
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

    # 先清理外部路径泄漏，再注入项目自有路径
    _sanitize_sys_path()

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
