"""Runtime dependency bootstrap for footage-gate.

The desktop build can load a freshly-added plugin before the host has a
usable ``numpy`` on ``sys.path``.  ``plugin.py`` registers routes immediately,
then imports the local pipeline only when a task actually runs.  That pipeline
imports ``footage_gate_ffmpeg`` / ``footage_gate_silence`` / ``footage_gate_qc``;
all three need NumPy.

This helper keeps the fallback local to the plugin:

1. Probe the plugin-managed ``deps/`` directory and the per-plugin optional
   modules directory under ``~/.openakita/modules/footage-gate/site-packages``.
2. If the import still fails, install the missing wheel into that per-plugin
   optional modules directory.
3. Retry the import after updating ``sys.path``.
"""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLUGIN_ID = "footage-gate"
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
    for candidate in _candidate_site_dirs(plugin_dir):
        if not candidate.is_dir():
            continue
        path = str(candidate)
        if path in sys.path:
            continue
        sys.path.append(path)
        appended.append(path)
    return appended


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
            logger.warning("footage-gate dependency install timed out: %s", last_error)
            continue
        except Exception as exc:  # noqa: BLE001 - retry with next mirror
            last_error = f"spawn error via {url}: {exc}"
            logger.warning("footage-gate dependency install failed to start: %s", last_error)
            continue

        if proc.returncode == 0:
            logger.info("footage-gate dependency install ok via %s: %s", url, specs)
            return True, ""

        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        last_error = f"pip exit {proc.returncode} via {url}: {tail}"
        logger.warning("footage-gate dependency install failed: %s", last_error)
    return False, last_error


def _drop_stale_modules(import_name: str) -> None:
    for stale in [m for m in sys.modules if m == import_name or m.startswith(import_name + ".")]:
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
            logger.info("footage-gate: %s imported after sys.path update", label)
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
                f"Could not auto-install {label} ({pip_spec}). "
                f"All mirrors failed: {err}. "
                f"Manual command: {_resolve_python_executable()} -m pip install "
                f"--target {target} {pip_spec}"
            )

        target_str = str(target)
        if target_str not in sys.path:
            sys.path.append(target_str)
        importlib.invalidate_caches()
        _drop_stale_modules(import_name)
        try:
            module = importlib.import_module(import_name)
        except ImportError as exc:
            raise DepInstallFailed(
                f"Installed {label} into {target}, but import still failed: {exc}",
                cause=exc,
            ) from exc
        _RESOLVED.add(import_name)
        return module


def preinstall_async(
    specs: list[tuple[str, str]],
    *,
    plugin_dir: Path | None = None,
) -> None:
    """Install optional runtime deps in the background after plugin load."""

    if not specs:
        return

    def _worker() -> None:
        for import_name, pip_spec in specs:
            try:
                ensure_importable(import_name, pip_spec, plugin_dir=plugin_dir)
            except Exception as exc:  # noqa: BLE001 - background best effort
                logger.info("footage-gate preinstall %s skipped: %s", import_name, exc)

    threading.Thread(
        target=_worker,
        name="footage-gate-dep-bootstrap",
        daemon=True,
    ).start()
