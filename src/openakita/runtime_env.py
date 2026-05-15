"""
运行时环境检测 - 兼容 PyInstaller 打包和常规 Python 环境

PyInstaller 打包后 sys.executable 指向 openakita-server.exe 而非 Python 解释器，
本模块提供统一的运行时环境检测层，确保 pip install / 脚本执行等功能正常工作。
"""

import json
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

IS_FROZEN = getattr(sys, "frozen", False)
"""是否在 PyInstaller 打包环境中运行"""

PYTHON_ENV_BLOCKLIST = {
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "CONDA_SHLVL",
    "CONDA_PYTHON_EXE",
}

PIP_ENV_BLOCKLIST = {
    "PIP_TARGET",
    "PIP_PREFIX",
    "PIP_USER",
    "PIP_REQUIRE_VIRTUALENV",
}

TOOLCHAIN_ENV_BLOCKLIST = {
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "NODE_TLS_REJECT_UNAUTHORIZED",
    "NODE_PATH",
    "NPM_CONFIG_PREFIX",
    "NPM_CONFIG_CACHE",
    "npm_config_prefix",
    "npm_config_cache",
    "COREPACK_HOME",
}

LINUX_DYNAMIC_LIBRARY_ENV_BLOCKLIST = {
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "LIBRARY_PATH",
    "PKG_CONFIG_PATH",
}

OPENAKITA_SECRET_ENV_BLOCKLIST = {
    "OPENAKITA_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "AZURE_CLIENT_SECRET",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_RUNTIME_URL",
}

OPENAKITA_SECRET_ENV_PREFIXES = (
    "OPENAKITA_FORCE_",
)


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


def get_configured_venv_path() -> str | None:
    """获取虚拟环境路径（供提示词构建等模块使用）。

    优先级: 从当前 Python 解释器路径推断。
    """
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
    """获取 OpenAkita 根目录路径 (避免循环导入 config)。

    优先使用 OPENAKITA_ROOT 环境变量，默认 ~/.openakita。
    """
    import os

    env_root = os.environ.get("OPENAKITA_ROOT", "").strip()
    if env_root:
        return Path(env_root)
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
      1. 工作区 venv ({project_root}/data/venv/)
      2. 全局 venv (~/.openakita/venv/)
      3. 打包内置 Python (_internal/python.exe)

    常规开发环境下: 返回 sys.executable
    """
    if not IS_FROZEN:
        return sys.executable

    # 1. 检查 {project_root}/data/venv/ — 工作区虚拟环境
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

    # 2. 检查 ~/.openakita/venv/
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

    # 3. 打包内置 Python（_internal/ 目录，构建时捆绑的同版本 Python + pip）
    bundled = _get_bundled_internal_python()
    if bundled:
        return bundled

    logger.warning(
        "未找到项目自带的 Python 解释器。"
        "已搜索: 工作区 venv → ~/.openakita/venv → "
        "打包内置 Python。"
        "请重新安装 OpenAkita，确保安装包资源完整。"
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

PIP_INDEX_PRESETS: dict[str, dict[str, str]] = {
    "aliyun": {
        "id": "aliyun",
        "label": "Aliyun PyPI",
        "url": "https://mirrors.aliyun.com/pypi/simple/",
        "trusted_host": "mirrors.aliyun.com",
    },
    "tuna": {
        "id": "tuna",
        "label": "Tsinghua PyPI",
        "url": "https://pypi.tuna.tsinghua.edu.cn/simple/",
        "trusted_host": "pypi.tuna.tsinghua.edu.cn",
    },
    "ustc": {
        "id": "ustc",
        "label": "USTC PyPI",
        "url": "https://pypi.mirrors.ustc.edu.cn/simple/",
        "trusted_host": "pypi.mirrors.ustc.edu.cn",
    },
    "official": {
        "id": "official",
        "label": "Official PyPI",
        "url": "https://pypi.org/simple/",
        "trusted_host": "",
    },
}


def get_runtime_root() -> Path:
    """Return the dual-venv runtime root under ~/.openakita/runtime."""
    return _get_openakita_root() / "runtime"


def get_runtime_manifest_path() -> Path:
    return get_runtime_root() / "manifest.json"


def get_runtime_logs_dir() -> Path:
    return get_runtime_root() / "logs"


def get_runtime_cache_dir() -> Path:
    return get_runtime_root() / "cache"


def get_toolchain_root() -> Path:
    return _get_openakita_root() / "toolchains"


def get_toolchain_cache_root() -> Path:
    return get_runtime_cache_dir() / "toolchains"


def get_readonly_seed_roots() -> list[Path]:
    """Return readonly seed/cache roots declared by env or bootstrap manifest.

    Seed roots are never written by OpenAkita. They are only used as verified
    fallback locations for enterprise/offline pre-provisioned resources.
    """
    roots: list[Path] = []
    raw_env = os.environ.get("OPENAKITA_READONLY_SEED_DIRS", "").strip()
    if raw_env:
        for item in raw_env.split(os.pathsep):
            if item.strip():
                roots.append(Path(item).expanduser())

    manifest = read_bootstrap_manifest()
    for raw in manifest.get("seed_dirs", []) if isinstance(manifest.get("seed_dirs"), list) else []:
        if isinstance(raw, str) and raw.strip():
            path = _resolve_manifest_relative_path(raw.strip()) or Path(raw.strip()).expanduser()
            roots.append(path)

    bootstrap_manifest = get_bootstrap_manifest_path()
    if bootstrap_manifest:
        default_seed = bootstrap_manifest.parent / "seed"
        if default_seed.exists():
            roots.append(default_seed)

    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        key = str(resolved)
        if key not in seen and resolved.exists():
            seen.add(key)
            out.append(resolved)
    return out


def _seed_candidates(kind: str, names: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for root in get_readonly_seed_roots():
        for name in names:
            candidates.extend(
                [
                    root / kind / name,
                    root / name,
                ]
            )
    return candidates


def get_bootstrap_manifest_path() -> Path | None:
    candidates: list[Path] = []
    env_bootstrap = os.environ.get("OPENAKITA_BOOTSTRAP_DIR", "").strip()
    if env_bootstrap:
        candidates.append(Path(env_bootstrap).expanduser() / "manifest.json")

    exe = Path(sys.executable).resolve()
    candidates.extend(
        [
            exe.parent / "bootstrap" / "manifest.json",
            exe.parent.parent / "bootstrap" / "manifest.json",
            exe.parent / "resources" / "bootstrap" / "manifest.json",
            exe.parent.parent / "resources" / "bootstrap" / "manifest.json",
        ]
    )

    # In dual-venv mode sys.executable points at runtime/app-venv/Scripts/python.exe,
    # while the immutable bootstrap resources live next to the installed desktop app.
    # The venv's pyvenv.cfg keeps the seed Python path in `home = .../bootstrap/python`.
    pyvenv_cfg = exe.parent.parent / "pyvenv.cfg"
    try:
        for line in pyvenv_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.lower().startswith("home"):
                continue
            _, raw_home = line.split("=", 1)
            home = Path(raw_home.strip()).expanduser()
            candidates.extend(
                [
                    home.parent / "manifest.json",
                    home.parent.parent / "bootstrap" / "manifest.json",
                    home.parent / "bootstrap" / "manifest.json",
                ]
            )
            break
    except (OSError, ValueError):
        pass

    for path in candidates:
        if path.is_file():
            return path
    return None


def read_bootstrap_manifest() -> dict:
    path = get_bootstrap_manifest_path()
    if not path:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_manifest_relative_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    manifest_path = get_bootstrap_manifest_path()
    if manifest_path:
        return manifest_path.parent / path
    return None


def get_managed_python_seed() -> str | None:
    manifest = read_bootstrap_manifest()
    seed = manifest.get("python_seed")
    if isinstance(seed, dict):
        candidate = _resolve_manifest_relative_path(str(seed.get("path") or ""))
        if candidate and candidate.exists() and verify_python_executable(str(candidate)):
            return str(candidate)

    bootstrap_manifest = get_bootstrap_manifest_path()
    if bootstrap_manifest:
        base = bootstrap_manifest.parent / "python"
        candidates = (
            [base / "python.exe", base / "bin" / "python.exe"]
            if sys.platform == "win32"
            else [base / "bin" / "python3", base / "bin" / "python", base / "python3", base / "python"]
        )
        for candidate in candidates:
            if candidate.exists() and verify_python_executable(str(candidate)):
                return str(candidate)
    for candidate in _seed_candidates(
        "python",
        ["python.exe", "bin/python.exe"]
        if sys.platform == "win32"
        else ["bin/python3", "bin/python", "python3", "python"],
    ):
        if candidate.exists() and verify_python_executable(str(candidate)):
            return str(candidate)
    return None


def get_managed_node_seed() -> str | None:
    manifest = read_bootstrap_manifest()
    seed = manifest.get("node_seed")
    if isinstance(seed, dict):
        candidate = _resolve_manifest_relative_path(str(seed.get("path") or ""))
        if candidate and candidate.exists():
            return str(candidate)
    bootstrap_manifest = get_bootstrap_manifest_path()
    if bootstrap_manifest:
        base = bootstrap_manifest.parent / "node"
        candidates = (
            [base / "node.exe", base / "bin" / "node.exe"]
            if sys.platform == "win32"
            else [base / "bin" / "node", base / "node"]
        )
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
    for candidate in _seed_candidates(
        "node",
        ["node.exe", "bin/node.exe"] if sys.platform == "win32" else ["bin/node", "node"],
    ):
        if candidate.exists():
            return str(candidate)
    return None


def get_managed_node_bin_dir() -> str | None:
    node = get_managed_node_seed()
    if not node:
        return None
    return str(Path(node).parent)


def get_workspace_dependency_cache_root() -> Path:
    return get_runtime_cache_dir() / "workspace-deps"


def resolve_toolchain_command(command: str) -> str | None:
    """Resolve a runtime-managed toolchain command before consulting PATH."""
    normalized = command.lower()
    suffix = ".exe" if sys.platform == "win32" else ""
    names = {
        "node": [f"node{suffix}"],
        "npm": [f"npm{'.cmd' if sys.platform == 'win32' else ''}", "npm"],
        "npx": [f"npx{'.cmd' if sys.platform == 'win32' else ''}", "npx"],
        "corepack": [f"corepack{'.cmd' if sys.platform == 'win32' else ''}", "corepack"],
        "pnpm": [f"pnpm{'.cmd' if sys.platform == 'win32' else ''}", "pnpm"],
        "yarn": [f"yarn{'.cmd' if sys.platform == 'win32' else ''}", "yarn"],
    }.get(normalized, [command])

    node_bin = get_managed_node_bin_dir()
    if node_bin:
        for name in names:
            candidate = Path(node_bin) / name
            if candidate.exists():
                return str(candidate)
    for root in _seed_candidates("node", names):
        if root.exists():
            return str(root)
    return shutil.which(command)


def get_app_venv_path() -> Path:
    return get_runtime_root() / "app-venv"


def get_agent_venv_path() -> Path:
    return get_runtime_root() / "agent-venv"


def ensure_runtime_layout() -> dict[str, str]:
    """Create the standard dual-venv runtime directory layout."""
    runtime_root = get_runtime_root()
    paths = {
        "runtime_root": runtime_root,
        "manifest": get_runtime_manifest_path(),
        "app_venv": get_app_venv_path(),
        "agent_venv": get_agent_venv_path(),
        "cache": get_runtime_cache_dir(),
        "cache_wheels": get_runtime_cache_dir() / "wheels",
        "cache_uv": get_runtime_cache_dir() / "uv",
        "cache_python": get_runtime_cache_dir() / "python",
        "logs": get_runtime_logs_dir(),
    }
    for key, path in paths.items():
        if key == "manifest":
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
    return {key: str(path) for key, path in paths.items()}


def read_runtime_manifest() -> dict:
    try:
        return json.loads(get_runtime_manifest_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _venv_python(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _venv_bin_dir(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts"
    return venv_root / "bin"


def resolve_pip_index() -> dict[str, str]:
    """Resolve the effective PyPI mirror for bootstrap, tools, and channel deps.

    Priority follows the migration plan:
    runtime manifest/settings -> OPENAKITA_PIP_INDEX_URL -> PIP_INDEX_URL -> Aliyun.
    """
    manifest = read_runtime_manifest()
    pip_index = manifest.get("pip_index")
    if isinstance(pip_index, dict) and pip_index.get("url"):
        return {
            "id": str(pip_index.get("id") or "custom"),
            "url": str(pip_index["url"]),
            "trusted_host": str(pip_index.get("trusted_host") or _trusted_host_for_url(pip_index["url"])),
        }

    env_url = os.environ.get("OPENAKITA_PIP_INDEX_URL", "").strip()
    if env_url:
        return {
            "id": "env-openakita",
            "url": env_url,
            "trusted_host": os.environ.get("OPENAKITA_PIP_TRUSTED_HOST", "").strip()
            or _trusted_host_for_url(env_url),
        }

    pip_url = os.environ.get("PIP_INDEX_URL", "").strip()
    if pip_url:
        return {
            "id": "env-pip",
            "url": pip_url,
            "trusted_host": os.environ.get("PIP_TRUSTED_HOST", "").strip()
            or _trusted_host_for_url(pip_url),
        }

    return PIP_INDEX_PRESETS["aliyun"].copy()


def _trusted_host_for_url(index_url: str) -> str:
    return index_url.split("//", 1)[1].split("/", 1)[0] if "//" in index_url else ""


def get_pip_install_args(packages: list[str], *, index_url: str | None = None) -> list[str]:
    """Return common pip install args without choosing the Python executable."""
    index = resolve_pip_index()
    effective_index = index_url or index["url"]
    trusted_host = index["trusted_host"] if effective_index == index["url"] else _trusted_host_for_url(effective_index)
    args = ["-m", "pip", "install", "-i", effective_index]
    if trusted_host:
        args.extend(["--trusted-host", trusted_host])
    args.extend(["--prefer-binary", *packages])
    return args


def sanitize_runtime_environment(
    env: dict[str, str] | None = None,
    *,
    include_pip: bool = True,
    include_ssl: bool = True,
    include_dynamic_libraries: bool = False,
    scrub_secrets: bool = False,
) -> dict[str, str]:
    """Return an OpenAkita-managed subprocess environment.

    This is the Python-side counterpart of the Tauri RuntimeManager env
    builder. It is intentionally copy-returning so call sites do not mutate the
    long-lived backend process environment while preparing pip/tools/MCP
    subprocesses.
    """
    merged = dict(os.environ if env is None else env)
    blocklist = set(PYTHON_ENV_BLOCKLIST)
    if include_pip:
        blocklist |= PIP_ENV_BLOCKLIST
    if include_ssl:
        blocklist |= TOOLCHAIN_ENV_BLOCKLIST
    if include_dynamic_libraries and sys.platform.startswith("linux"):
        blocklist |= LINUX_DYNAMIC_LIBRARY_ENV_BLOCKLIST
    for key in blocklist:
        merged.pop(key, None)
    if scrub_secrets:
        for key in list(merged):
            if key in OPENAKITA_SECRET_ENV_BLOCKLIST:
                merged.pop(key, None)
    merged["PYTHONNOUSERSITE"] = "1"
    merged["OPENAKITA_ENV_TRUST_SOURCE"] = merged.get("OPENAKITA_ENV_TRUST_SOURCE", "runtime-api")
    return merged


def apply_runtime_pip_environment(
    env: dict[str, str] | None = None,
    *,
    python_executable: str | None = None,
) -> dict[str, str]:
    """Environment for venv/pip creation and extension dependency installs."""
    merged = sanitize_runtime_environment(
        env,
        include_pip=True,
        include_ssl=True,
        include_dynamic_libraries=True,
        scrub_secrets=True,
    )
    merged["PYTHONUTF8"] = "1"
    merged["PYTHONIOENCODING"] = "utf-8"
    merged["OPENAKITA_SUBPROCESS_SECRET_SCRUB"] = "1"
    pip_index = resolve_pip_index()
    merged["PIP_INDEX_URL"] = pip_index["url"]
    merged["UV_INDEX_URL"] = pip_index["url"]
    if pip_index.get("trusted_host"):
        merged["PIP_TRUSTED_HOST"] = pip_index["trusted_host"]

    if python_executable:
        py_path = Path(python_executable)
        if IS_FROZEN and py_path.parent.name == "_internal":
            path_parts = [str(py_path.parent)]
            for sub in ("Lib", "DLLs"):
                p = py_path.parent / sub
                if p.is_dir():
                    path_parts.append(str(p))
            merged["PYTHONPATH"] = os.pathsep.join(path_parts)
    return merged


def apply_subprocess_secret_scrub(env: dict[str, str]) -> dict[str, str]:
    """Remove OpenAkita/provider secrets unless explicitly force-injected."""
    merged: dict[str, str] = {}
    for key, value in env.items():
        force_prefix = next((p for p in OPENAKITA_SECRET_ENV_PREFIXES if key.startswith(p)), "")
        if force_prefix:
            real_key = key[len(force_prefix):]
            merged[real_key] = value
            continue
        if key in OPENAKITA_SECRET_ENV_BLOCKLIST:
            continue
        merged[key] = value
    merged["OPENAKITA_SUBPROCESS_SECRET_SCRUB"] = "1"
    return merged


def build_user_subprocess_environment(
    env: dict[str, str] | None = None,
    *,
    scrub_secrets: bool = True,
) -> dict[str, str]:
    """Environment for user-facing tools, MCP stdio, hooks, and skills.

    This keeps normal shell/project context while applying OpenAkita's minimal
    Python toolchain injection and default secret scrub policy.

    ``env`` is an overrides map, not a complete environment. User-facing
    subprocesses need the ordinary host context (PATH, HOME/USERPROFILE,
    locale, proxy, Git/SSH config), while OpenAkita still scrubs runtime
    pollution and provider secrets.
    """
    base = dict(os.environ)
    if env:
        base.update(env)
    merged = apply_agent_python_environment(base)
    merged = apply_managed_node_environment(merged)
    merged["OPENAKITA_ENV_TRUST_SOURCE"] = "user-subprocess"
    if scrub_secrets:
        merged = apply_subprocess_secret_scrub(merged)
    return merged


def apply_managed_node_environment(env: dict[str, str]) -> dict[str, str]:
    """Prefer OpenAkita-managed Node/npm/corepack for agent/tool subprocesses."""
    merged = dict(env)
    node = get_managed_node_seed()
    node_bin = get_managed_node_bin_dir()
    cache_root = get_workspace_dependency_cache_root()
    npm_prefix = cache_root / "npm-prefix"
    npm_cache = cache_root / "npm-cache"
    corepack_home = cache_root / "corepack"
    for path in (npm_prefix, npm_cache, corepack_home):
        path.mkdir(parents=True, exist_ok=True)
    if node:
        merged["OPENAKITA_NODE"] = node
    if node_bin:
        merged["OPENAKITA_NODE_BIN"] = node_bin
        merged["PATH"] = node_bin + os.pathsep + merged.get("PATH", "")
    merged["NPM_CONFIG_PREFIX"] = str(npm_prefix)
    merged["NPM_CONFIG_CACHE"] = str(npm_cache)
    merged["COREPACK_HOME"] = str(corepack_home)
    merged["OPENAKITA_WORKSPACE_DEPS_CACHE"] = str(cache_root)
    return merged


def get_app_python_executable() -> str | None:
    env_py = os.environ.get("OPENAKITA_APP_PYTHON", "").strip()
    if env_py and verify_python_executable(env_py):
        return env_py

    app_py = _venv_python(get_app_venv_path())
    if app_py.exists() and verify_python_executable(str(app_py)):
        return str(app_py)

    if not IS_FROZEN:
        return sys.executable
    return None


def get_agent_python_executable() -> str | None:
    env_py = os.environ.get("OPENAKITA_AGENT_PYTHON", "").strip()
    if env_py and verify_python_executable(env_py):
        return env_py

    agent_py = _venv_python(get_agent_venv_path())
    if agent_py.exists() and verify_python_executable(str(agent_py)):
        return str(agent_py)

    if not IS_FROZEN:
        return sys.executable
    return None


def get_agent_bin_dir() -> str | None:
    agent_py = get_agent_python_executable()
    if not agent_py:
        return None
    py_path = Path(agent_py)
    if py_path.parent.name in ("Scripts", "bin"):
        return str(py_path.parent)
    return str(_venv_bin_dir(get_agent_venv_path()))


def apply_agent_python_environment(env: dict[str, str]) -> dict[str, str]:
    """Return env with agent-venv Python/pip naturally preferred."""
    merged = sanitize_runtime_environment(
        env,
        include_pip=True,
        include_ssl=False,
        include_dynamic_libraries=False,
        scrub_secrets=True,
    )
    agent_py = get_agent_python_executable()
    agent_bin = get_agent_bin_dir()
    pip_index = resolve_pip_index()

    if agent_py:
        merged["OPENAKITA_AGENT_PYTHON"] = agent_py
    if agent_bin:
        merged["OPENAKITA_AGENT_BIN"] = agent_bin
        merged["PATH"] = agent_bin + os.pathsep + merged.get("PATH", "")

    app_py = get_app_python_executable()
    if app_py:
        merged["OPENAKITA_APP_PYTHON"] = app_py

    merged["PIP_INDEX_URL"] = pip_index["url"]
    merged["UV_INDEX_URL"] = pip_index["url"]
    if pip_index.get("trusted_host"):
        merged["PIP_TRUSTED_HOST"] = pip_index["trusted_host"]
    merged["OPENAKITA_SUBPROCESS_SECRET_SCRUB"] = "1"
    return merged


def get_agent_pip_command(packages: list[str], *, index_url: str | None = None) -> list[str] | None:
    py = get_agent_python_executable()
    if not py:
        return None
    return [py, *get_pip_install_args(packages, index_url=index_url)]


def get_runtime_environment_report() -> dict:
    manifest = read_runtime_manifest()
    app_py = get_app_python_executable()
    agent_py = get_agent_python_executable()
    host_py = get_python_executable()
    pip_index = resolve_pip_index()
    legacy_mode = bool(manifest.get("legacy_mode"))
    bootstrap_manifest = read_bootstrap_manifest()
    managed_python = get_managed_python_seed()
    managed_node = get_managed_node_seed()
    bootstrap_python_seed = bootstrap_manifest.get("python_seed")
    bootstrap_node_seed = bootstrap_manifest.get("node_seed")

    if legacy_mode:
        mode = "legacy-pyinstaller"
    elif app_py and agent_py and get_runtime_root() in Path(app_py).parents:
        mode = "dual-venv"
    elif IS_FROZEN:
        mode = "degraded"
    else:
        mode = "source"

    return {
        "mode": mode,
        "runtime_root": str(get_runtime_root()),
        "manifest": str(get_runtime_manifest_path()),
        "host_python": host_py,
        "sys_executable": sys.executable,
        "is_frozen": IS_FROZEN,
        "app_python": app_py,
        "app_venv": str(get_app_venv_path()),
        "agent_python": agent_py,
        "agent_venv": str(get_agent_venv_path()),
        "agent_bin": get_agent_bin_dir(),
        "toolchain_manifest": str(get_bootstrap_manifest_path() or ""),
        "toolchain_python": managed_python,
        "toolchain_node": managed_node,
        "toolchain_node_bin": get_managed_node_bin_dir(),
        "toolchain_cache_root": str(get_toolchain_cache_root()),
        "workspace_dependency_cache": str(get_workspace_dependency_cache_root()),
        "seed_dirs": [str(p) for p in get_readonly_seed_roots()],
        "bootstrap_python_seed": bootstrap_python_seed,
        "bootstrap_node_seed": bootstrap_node_seed,
        "bootstrap_python_seed_packaged": bool(
            isinstance(bootstrap_python_seed, dict) and bootstrap_python_seed.get("packaged")
        ),
        "bootstrap_node_seed_packaged": bool(
            isinstance(bootstrap_node_seed, dict) and bootstrap_node_seed.get("packaged")
        ),
        "python_abi": bootstrap_manifest.get("python_abi") or bootstrap_manifest.get("python_version"),
        "wheel_tag": bootstrap_manifest.get("wheel_tag", ""),
        "pip_install_target": "agent-venv" if agent_py else "unavailable",
        "pip_index_id": pip_index.get("id"),
        "pip_index_url": pip_index.get("url"),
        "pip_trusted_host": pip_index.get("trusted_host", ""),
        "can_pip_install": bool(agent_py),
        "legacy_mode": legacy_mode,
        "last_error": manifest.get("last_error"),
    }


def _probe_python_tool(py: str | None, args: list[str], *, timeout: int = 8) -> str:
    """Return a short version/probe string for a Python-backed command."""
    if not py:
        return "unavailable"
    import subprocess

    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run([py, *args], **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"probe failed: {type(exc).__name__}: {exc}"
    output = (proc.stdout or proc.stderr or "").strip().splitlines()
    text = output[-1] if output else f"exit {proc.returncode}"
    if proc.returncode != 0:
        return f"exit {proc.returncode}: {text}"
    return text


def _probe_uv_version() -> str:
    import subprocess

    candidates: list[str] = []
    env_uv = os.environ.get("OPENAKITA_UV", "").strip()
    if env_uv:
        candidates.append(env_uv)
    candidates.append("uv")
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 8,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    for candidate in candidates:
        try:
            proc = subprocess.run([candidate, "--version"], **kwargs)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return (proc.stdout or proc.stderr or "").strip()
    return "unavailable"


def log_runtime_environment_report() -> None:
    """Log the effective runtime contract once during service startup.

    This makes packaged-only failures diagnosable: the log records which Python
    executable owns host/plugin pip installs, which dual-venv interpreters are
    active, and which PyPI mirror pip/uv will use.
    """
    try:
        report = get_runtime_environment_report()
        host_py = report.get("host_python")
        app_py = report.get("app_python")
        agent_py = report.get("agent_python")
        logger.info(
            "[runtime] mode=%s frozen=%s sys_executable=%s host_python=%s "
            "app_python=%s agent_python=%s pip_index=%s trusted_host=%s "
            "can_pip_install=%s legacy_mode=%s last_error=%s",
            report.get("mode"),
            report.get("is_frozen"),
            report.get("sys_executable"),
            host_py,
            app_py,
            agent_py,
            report.get("pip_index_url"),
            report.get("pip_trusted_host"),
            report.get("can_pip_install"),
            report.get("legacy_mode"),
            report.get("last_error"),
        )
        logger.info(
            "[runtime] versions: host=%s app=%s agent=%s host_pip=%s app_pip=%s "
            "agent_pip=%s uv=%s",
            _probe_python_tool(host_py, ["--version"]),
            _probe_python_tool(app_py, ["--version"]),
            _probe_python_tool(agent_py, ["--version"]),
            _probe_python_tool(host_py, ["-m", "pip", "--version"]),
            _probe_python_tool(app_py, ["-m", "pip", "--version"]),
            _probe_python_tool(agent_py, ["-m", "pip", "--version"]),
            _probe_uv_version(),
        )
    except Exception:
        logger.debug("[runtime] failed to log runtime environment report", exc_info=True)


def get_pip_command(packages: list[str], *, index_url: str | None = None) -> list[str] | None:
    """获取 pip install 命令列表（默认使用国内镜像源）。

    Args:
        packages: 要安装的包名列表
        index_url: 自定义镜像源 URL，为 None 时使用阿里云镜像

    Returns:
        命令参数列表，若不支持则返回 None。
    """
    return get_agent_pip_command(packages, index_url=index_url)


def get_channel_deps_dir() -> Path:
    """获取 IM 通道依赖的隔离安装目录。

    路径: ~/.openakita/modules/channel-deps/site-packages
    该目录会被 inject_module_paths() 自动扫描并注入到 sys.path。
    """
    return _get_openakita_root() / "modules" / "channel-deps" / "site-packages"


def get_channel_deps_seed_dirs() -> list[Path]:
    return [p for p in _seed_candidates("channel-deps", ["site-packages"]) if p.is_dir()]


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
        logger.warning("Removed invalid SSL_CERT_FILE. httpx will fall back to certifi default.")
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

    # 来源 2：只读 seed/cache 预置依赖（离线/企业分发，不写入）
    for sp in get_channel_deps_seed_dirs():
        if str(sp) not in sys.path:
            sys.path.append(str(sp))
            injected.append(f"seed:{sp.parent.name}")

    # 来源 3：扫描 ~/.openakita/modules/*/site-packages（兜底）
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
        ("torch", "lib"),  # PyTorch: c10.dll, torch_cpu.dll, libiomp5md.dll
        ("torch", "bin"),  # PyTorch 某些版本把 DLL 放在 bin/
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
