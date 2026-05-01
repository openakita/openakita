"""Runtime dependency bootstrap for omni-post.

The desktop build can load a freshly-installed plugin before the host has
added plugin-managed wheels to ``sys.path``.  ``plugin.py`` imports
``omni_post_cookies`` at module load time, and that module needs
``cryptography.fernet`` for Fernet cookie encryption.

This helper keeps the fallback local to the plugin:

1. Probe ``<plugin_dir>/deps`` and the per-plugin optional modules dir under
   ``~/.openakita/modules/omni-post/site-packages``.
2. If the import still fails, install the missing wheel into that private dir.
3. Retry the import after updating ``sys.path``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLUGIN_ID = "omni-post"
_INSTALL_LOCK = threading.RLock()
_RESOLVED: set[str] = set()


class DepInstallFailed(RuntimeError):
    """Raised when a dependency could not be imported or auto-installed."""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause


def _openakita_root() -> Path:
    env_root = os.environ.get("OPENAKITA_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    return Path.home() / ".openakita"


def _module_site_packages() -> Path:
    return _openakita_root() / "modules" / PLUGIN_ID / "site-packages"


def _candidate_site_dirs(plugin_dir: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if plugin_dir is not None:
        candidates.append(plugin_dir / "deps")
    candidates.append(_module_site_packages())

    exe_dir = Path(sys.executable).parent
    internal = exe_dir if exe_dir.name == "_internal" else exe_dir / "_internal"
    if internal.is_dir():
        candidates.append(internal / "Lib" / "site-packages")
    return candidates


def _ensure_on_syspath(plugin_dir: Path | None) -> list[str]:
    appended: list[str] = []
    existing_candidates = [
        candidate for candidate in _candidate_site_dirs(plugin_dir) if candidate.is_dir()
    ]
    for candidate in reversed(existing_candidates):
        # These dirs are plugin-private dependency stores. Put them before
        # bundled/host paths so a partial host copy of the same package cannot
        # shadow the freshly installed plugin copy.
        _promote_on_syspath(candidate)
        appended.append(str(candidate))
    return appended


def _promote_on_syspath(path_obj: Path) -> None:
    path = str(path_obj)
    try:
        sys.path.remove(path)
    except ValueError:
        pass
    sys.path.insert(0, path)


def _describe_import_state(import_name: str) -> str:
    root_name = import_name.partition(".")[0]
    parts: list[str] = []
    for name in (root_name, import_name):
        try:
            spec = importlib.util.find_spec(name)
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            parts.append(f"{name}=<find_spec error: {exc}>")
            continue
        if spec is None:
            parts.append(f"{name}=<not found>")
            continue
        origin = spec.origin or "<namespace>"
        locations = list(spec.submodule_search_locations or [])
        suffix = f", locations={locations}" if locations else ""
        parts.append(f"{name}={origin}{suffix}")
    return "; ".join(parts)


def _resolve_python_executable() -> str:
    if not getattr(sys, "frozen", False):
        return sys.executable

    exe_dir = Path(sys.executable).parent
    internal = exe_dir if exe_dir.name == "_internal" else exe_dir / "_internal"
    candidates = (
        [internal / "python.exe", internal / "python3.exe"]
        if sys.platform == "win32"
        else [internal / "python3", internal / "python"]
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _manual_install_command(pip_spec: str) -> str:
    return (
        f"{_resolve_python_executable()} -m pip install --target "
        f"{_module_site_packages()} {pip_spec}"
    )


_PIP_MIRRORS: list[tuple[str, str]] = [
    ("https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
    ("https://pypi.tuna.tsinghua.edu.cn/simple/", "pypi.tuna.tsinghua.edu.cn"),
    ("https://pypi.mirrors.ustc.edu.cn/simple/", "pypi.mirrors.ustc.edu.cn"),
    ("https://pypi.org/simple/", ""),
]


def _pip_install(specs: list[str], target: Path) -> tuple[bool, str]:
    target.mkdir(parents=True, exist_ok=True)
    py = _resolve_python_executable()

    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONPATH", None)
    py_path = Path(py)
    if getattr(sys, "frozen", False) and py_path.parent.name == "_internal":
        env["PYTHONHOME"] = str(py_path.parent)

    extra_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        extra_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    last_error = ""
    for url, trusted_host in _PIP_MIRRORS:
        cmd = [
            py,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--prefer-binary",
            "--target",
            str(target),
            "-i",
            url,
        ]
        if trusted_host:
            cmd.extend(["--trusted-host", trusted_host])
        cmd.extend(specs)

        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                **extra_kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"timeout via {url}: {exc}"
            logger.warning("omni-post dependency install timed out: %s", last_error)
            continue
        except Exception as exc:  # noqa: BLE001 - retry with next mirror
            last_error = f"spawn error via {url}: {exc}"
            logger.warning("omni-post dependency install failed to start: %s", last_error)
            continue

        if proc.returncode == 0:
            logger.info("omni-post dependency install ok via %s: %s", url, specs)
            return True, ""

        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        last_error = f"pip exit {proc.returncode} via {url}: {tail}"
        logger.warning("omni-post dependency install failed: %s", last_error)
    return False, last_error


def _drop_stale_modules(import_name: str) -> None:
    root_name = import_name.partition(".")[0]
    for stale in [
        m
        for m in sys.modules
        if m in (root_name, import_name) or m.startswith(root_name + ".")
    ]:
        sys.modules.pop(stale, None)


def ensure_importable(
    import_name: str,
    pip_spec: str,
    *,
    plugin_dir: Path | None = None,
    friendly_name: str | None = None,
) -> Any:
    """Return an imported module, installing it into plugin-private storage if needed."""

    if import_name in _RESOLVED:
        return importlib.import_module(import_name)

    label = friendly_name or import_name
    try:
        module = importlib.import_module(import_name)
        _RESOLVED.add(import_name)
        return module
    except ImportError:
        pass

    appended = _ensure_on_syspath(plugin_dir)
    if appended:
        importlib.invalidate_caches()
        _drop_stale_modules(import_name)
        try:
            module = importlib.import_module(import_name)
            _RESOLVED.add(import_name)
            logger.info("omni-post: %s imported after sys.path update", label)
            return module
        except ImportError:
            pass

    with _INSTALL_LOCK:
        if import_name in _RESOLVED:
            return importlib.import_module(import_name)

        target = _module_site_packages()
        ok, err = _pip_install([pip_spec], target)
        if not ok:
            raise DepInstallFailed(
                f"无法自动安装 {label}（{pip_spec}）。"
                f"插件已尝试多个镜像源全部失败：{err}\n"
                "请检查网络后重试，或手动执行：\n"
                f"  {_manual_install_command(pip_spec)}"
            )

        _promote_on_syspath(target)
        importlib.invalidate_caches()
        _drop_stale_modules(import_name)
        try:
            module = importlib.import_module(import_name)
        except ImportError as exc:
            raise DepInstallFailed(
                f"已安装 {label} 到 {target}，但仍无法导入：{exc}\n"
                f"导入解析状态：{_describe_import_state(import_name)}",
                cause=exc,
            ) from exc
        _RESOLVED.add(import_name)
        return module


def dependency_status(
    import_name: str,
    pip_spec: str,
    *,
    plugin_dir: Path | None = None,
    package_name: str | None = None,
    friendly_name: str | None = None,
) -> dict[str, Any]:
    """Return a UI-friendly snapshot for one plugin-private Python dependency."""

    label = friendly_name or package_name or import_name
    _ensure_on_syspath(plugin_dir)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(import_name)
    except Exception as exc:  # noqa: BLE001 - diagnostic surface
        return {
            "id": package_name or import_name.partition(".")[0],
            "label": label,
            "import_name": import_name,
            "pip_spec": pip_spec,
            "found": False,
            "version": "",
            "import_path": "",
            "target_dir": str(_module_site_packages()),
            "manual_command": _manual_install_command(pip_spec),
            "error": f"{type(exc).__name__}: {exc}",
            "import_state": _describe_import_state(import_name),
        }

    root_name = package_name or import_name.partition(".")[0]
    try:
        version = importlib.metadata.version(root_name)
    except importlib.metadata.PackageNotFoundError:
        version = getattr(importlib.import_module(root_name), "__version__", "") or ""
    return {
        "id": root_name,
        "label": label,
        "import_name": import_name,
        "pip_spec": pip_spec,
        "found": True,
        "version": str(version),
        "import_path": str(getattr(module, "__file__", "") or ""),
        "target_dir": str(_module_site_packages()),
        "manual_command": _manual_install_command(pip_spec),
        "error": "",
        "import_state": _describe_import_state(import_name),
    }


def preinstall_async(
    specs: list[tuple[str, str]],
    *,
    plugin_dir: Path | None = None,
) -> None:
    """Install runtime deps in the background after plugin load."""

    if not specs:
        return

    def _worker() -> None:
        for import_name, pip_spec in specs:
            try:
                ensure_importable(import_name, pip_spec, plugin_dir=plugin_dir)
            except Exception as exc:  # noqa: BLE001 - background best effort
                logger.info("omni-post preinstall %s skipped: %s", import_name, exc)

    threading.Thread(
        target=_worker,
        name="omni-post-dep-bootstrap",
        daemon=True,
    ).start()
