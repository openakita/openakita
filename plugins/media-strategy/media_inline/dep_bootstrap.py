# ruff: noqa: N999
"""Plugin-local dependency bootstrap for Media Strategy.

The module mirrors the lightweight parts of avatar-studio's dependency
bootstrap: probe plugin-local ``deps/`` first, then a private OpenAkita
module directory, and only install on demand. The plugin can therefore
open its Settings and Health pages even when optional RSS helpers are
missing.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from types import ModuleType
from typing import Any

PLUGIN_ID = "media-strategy"
_LOCK = threading.RLock()
_RESOLVED: set[str] = set()
_STATE: dict[str, dict[str, Any]] = {}
_LOGS: dict[str, deque[str]] = {}


class DepInstallFailed(Exception):
    """Raised when probing and pip installation both fail."""


def _openakita_root() -> Path:
    raw = os.environ.get("OPENAKITA_ROOT", "").strip()
    return Path(raw) if raw else Path.home() / ".openakita"


def _install_target() -> Path:
    return (
        _openakita_root()
        / "modules"
        / PLUGIN_ID
        / f"site-packages-py{sys.version_info.major}{sys.version_info.minor}-runtime"
    )


def _candidate_dirs(plugin_dir: Path | None) -> list[Path]:
    dirs: list[Path] = []
    if plugin_dir is not None:
        dirs.append(plugin_dir / "deps")
    dirs.append(_install_target())
    dirs.append(_openakita_root() / "modules" / PLUGIN_ID / "site-packages")
    exe_dir = Path(sys.executable).parent
    internal = exe_dir if exe_dir.name == "_internal" else exe_dir / "_internal"
    if internal.is_dir():
        dirs.append(internal / "Lib" / "site-packages")
    return dirs


def ensure_runtime_paths(plugin_dir: Path | str | None = None) -> list[str]:
    """Append known dependency directories to ``sys.path`` idempotently."""

    p = Path(plugin_dir) if plugin_dir is not None else None
    appended: list[str] = []
    for cand in _candidate_dirs(p):
        if not cand.is_dir():
            continue
        s = str(cand)
        if s in sys.path:
            continue
        if cand == _install_target():
            sys.path.insert(0, s)
        else:
            sys.path.append(s)
        appended.append(s)
    return appended


def _real_python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower().endswith(".exe"):
        internal = exe.parent / "_internal" / "python.exe"
        if internal.exists():
            return str(internal)
    return sys.executable


def _log(dep_id: str, message: str) -> None:
    _LOGS.setdefault(dep_id, deque(maxlen=80)).append(f"{time.strftime('%H:%M:%S')} {message}")


def _state(dep_id: str) -> dict[str, Any]:
    state = _STATE.setdefault(
        dep_id,
        {
            "busy": False,
            "last_error": "",
            "last_started_at": 0.0,
            "last_finished_at": 0.0,
            "last_success": False,
        },
    )
    state["log_tail"] = list(_LOGS.get(dep_id, ()))
    return dict(state)


def get_dep_state() -> dict[str, dict[str, Any]]:
    """Return dependency installation state for the health route."""

    for dep_id in ("feedparser", "bs4"):
        _state(dep_id)
    return {k: _state(k) for k in sorted(_STATE)}


def ensure_importable(
    import_name: str,
    pip_spec: str,
    *,
    plugin_dir: Path | str | None = None,
    friendly_name: str | None = None,
) -> ModuleType:
    """Import a dependency, installing it privately if needed."""

    dep_id = friendly_name or import_name
    ensure_runtime_paths(plugin_dir)
    if import_name in _RESOLVED:
        return importlib.import_module(import_name)
    try:
        module = importlib.import_module(import_name)
        _RESOLVED.add(import_name)
        _STATE[dep_id] = {**_state(dep_id), "last_success": True, "last_error": ""}
        return module
    except Exception as first_exc:  # noqa: BLE001
        with _LOCK:
            try:
                module = importlib.import_module(import_name)
                _RESOLVED.add(import_name)
                return module
            except Exception:
                pass
            target = _install_target()
            target.mkdir(parents=True, exist_ok=True)
            state = _STATE.setdefault(dep_id, _state(dep_id))
            state.update({"busy": True, "last_started_at": time.time(), "last_error": ""})
            _log(dep_id, f"installing {pip_spec} into {target}")
            cmd = [
                _real_python(),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--target",
                str(target),
                pip_spec,
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
            except Exception as exc:  # noqa: BLE001
                state.update(
                    {
                        "busy": False,
                        "last_success": False,
                        "last_error": str(exc),
                        "last_finished_at": time.time(),
                    }
                )
                _log(dep_id, f"install failed: {exc}")
                raise DepInstallFailed(f"{dep_id} is not importable and install failed") from first_exc
            ensure_runtime_paths(plugin_dir)
            importlib.invalidate_caches()
            try:
                module = importlib.import_module(import_name)
            except Exception as exc:  # noqa: BLE001
                state.update(
                    {
                        "busy": False,
                        "last_success": False,
                        "last_error": str(exc),
                        "last_finished_at": time.time(),
                    }
                )
                raise DepInstallFailed(f"{dep_id} installed but still not importable") from exc
            _RESOLVED.add(import_name)
            state.update(
                {
                    "busy": False,
                    "last_success": True,
                    "last_error": "",
                    "last_finished_at": time.time(),
                }
            )
            _log(dep_id, "install ok")
            return module


def preinstall_async(
    specs: list[tuple[str, str]],
    *,
    plugin_dir: Path | str | None = None,
) -> None:
    """Start a daemon thread that warms optional dependencies."""

    def _worker() -> None:
        for import_name, pip_spec in specs:
            try:
                ensure_importable(import_name, pip_spec, plugin_dir=plugin_dir)
            except Exception:
                continue

    threading.Thread(target=_worker, name=f"{PLUGIN_ID}-dep-preinstall", daemon=True).start()
