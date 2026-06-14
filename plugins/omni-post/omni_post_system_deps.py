"""System/runtime dependency manager for omni-post.

This mirrors the fire-and-poll shape used by seedance-video, but keeps the
catalog local to omni-post.  Python package dependencies stay in
``omni_post_dep_bootstrap.py``; this module only handles system/runtime
components such as FFmpeg and Playwright's Chromium browser cache.
"""

from __future__ import annotations

import asyncio
import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Platform = Literal["windows", "macos", "linux"]


def _current_platform() -> Platform:
    name = platform.system().lower()
    if name.startswith("win"):
        return "windows"
    if name == "darwin":
        return "macos"
    return "linux"


def _is_root() -> bool:
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return False


def _read_registry_path_windows() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg
    except Exception:
        return ""
    parts: list[str] = []
    for root, sub in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ):
        try:
            with winreg.OpenKey(root, sub) as key:
                val, _ = winreg.QueryValueEx(key, "Path")
                if val:
                    parts.append(str(val))
        except OSError:
            continue
    return os.pathsep.join(parts)


def _refresh_process_path_windows() -> bool:
    if os.name != "nt":
        return False
    extra = _read_registry_path_windows()
    if not extra:
        return False
    current = os.environ.get("PATH", "")
    seen = {p.strip().lower() for p in current.split(os.pathsep) if p.strip()}
    added: list[str] = []
    for entry in extra.split(os.pathsep):
        value = os.path.expandvars(entry.strip())
        if value and value.lower() not in seen:
            added.append(value)
            seen.add(value.lower())
    if added:
        os.environ["PATH"] = current + (os.pathsep if current else "") + os.pathsep.join(added)
    return bool(added)


_WELL_KNOWN_BIN_GLOBS: dict[str, list[str]] = {
    "ffmpeg": [
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffmpeg.exe",
        r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe",
        r"%PROGRAMDATA%\chocolatey\bin\ffmpeg.exe",
        r"%USERPROFILE%\scoop\shims\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"%PROGRAMFILES%\ffmpeg\bin\ffmpeg.exe",
    ],
    "ffprobe": [
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffprobe.exe",
        r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffprobe.exe",
        r"%PROGRAMDATA%\chocolatey\bin\ffprobe.exe",
        r"%USERPROFILE%\scoop\shims\ffprobe.exe",
        r"C:\ffmpeg\bin\ffprobe.exe",
        r"%PROGRAMFILES%\ffmpeg\bin\ffprobe.exe",
    ],
}


def _scan_well_known_paths_windows(probe: str) -> str:
    if os.name != "nt":
        return ""
    for pattern in _WELL_KNOWN_BIN_GLOBS.get(probe, []):
        try:
            for hit in glob.glob(os.path.expandvars(pattern), recursive=True):
                if os.path.isfile(hit):
                    return hit
        except OSError:
            continue
    return ""


def _openakita_root() -> Path:
    env_root = os.environ.get("OPENAKITA_ROOT", "").strip()
    return Path(env_root) if env_root else Path.home() / ".openakita"


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


@dataclass(frozen=True)
class InstallMethod:
    platform: Platform
    strategy: str
    command: tuple[str, ...] | None
    description: str
    requires_sudo: bool = False
    estimated_seconds: int = 120
    manual_url: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "strategy": self.strategy,
            "description": self.description,
            "requires_sudo": self.requires_sudo,
            "estimated_seconds": self.estimated_seconds,
            "manual_url": self.manual_url,
            "command_hint": " ".join(self.command) if self.command else "",
        }


@dataclass(frozen=True)
class DepSpec:
    id: str
    display_name: str
    description: str
    homepage: str
    kind: Literal["binary", "playwright"]
    probes: tuple[str, ...] = ()
    version_argv: tuple[str, ...] = ()
    version_regex: str = ""
    install_methods: tuple[InstallMethod, ...] = ()


def _playwright_install_method() -> InstallMethod:
    py = _resolve_python_executable()
    return InstallMethod(
        platform=_current_platform(),
        strategy="playwright",
        command=(py, "-m", "playwright", "install", "chromium"),
        description="Install Playwright Chromium browser cache.",
        estimated_seconds=180,
    )


_SPECS: dict[str, DepSpec] = {
    "ffmpeg": DepSpec(
        id="ffmpeg",
        display_name="FFmpeg",
        description="Generates video thumbnails for uploaded assets.",
        homepage="https://ffmpeg.org/download.html",
        kind="binary",
        probes=("ffmpeg",),
        version_argv=("ffmpeg", "-version"),
        version_regex=r"ffmpeg version\s+(\S+)",
        install_methods=(
            InstallMethod(
                platform="windows",
                strategy="winget",
                command=(
                    "winget",
                    "install",
                    "--id",
                    "Gyan.FFmpeg",
                    "-e",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ),
                description="Install FFmpeg via Windows Package Manager.",
            ),
            InstallMethod(
                platform="macos",
                strategy="brew",
                command=("brew", "install", "ffmpeg"),
                description="Install FFmpeg via Homebrew.",
                estimated_seconds=180,
            ),
            InstallMethod(
                platform="linux",
                strategy="apt",
                command=("apt-get", "install", "-y", "ffmpeg"),
                description="Install FFmpeg via apt. Requires root.",
                requires_sudo=True,
            ),
        ),
    ),
    "ffprobe": DepSpec(
        id="ffprobe",
        display_name="FFprobe",
        description="Extracts metadata from uploaded media.",
        homepage="https://ffmpeg.org/download.html",
        kind="binary",
        probes=("ffprobe",),
        version_argv=("ffprobe", "-version"),
        version_regex=r"ffprobe version\s+(\S+)",
        install_methods=(),
    ),
    "playwright-chromium": DepSpec(
        id="playwright-chromium",
        display_name="Playwright Chromium",
        description="Browser runtime used by the self-developed publishing engine.",
        homepage="https://playwright.dev/python/docs/browsers",
        kind="playwright",
        install_methods=(_playwright_install_method(),),
    ),
}


@dataclass
class RunState:
    busy: bool = False
    op_kind: str = ""
    started_at: float = 0.0
    error: str = ""
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=50))
    task: asyncio.Task[Any] | None = None


class OmniPostSystemDeps:
    """Detect and install runtime components used by omni-post."""

    def __init__(self) -> None:
        self._platform = _current_platform()
        self._state: dict[str, RunState] = {dep_id: RunState() for dep_id in _SPECS}
        self._locks: dict[str, asyncio.Lock] = {dep_id: asyncio.Lock() for dep_id in _SPECS}

    def list_components(self) -> list[dict[str, Any]]:
        return [self.status(dep_id) for dep_id in _SPECS]

    def status(self, dep_id: str) -> dict[str, Any]:
        spec = self._spec(dep_id)
        detected = self._detect(spec)
        state = self._state[dep_id]
        return {
            **detected,
            "methods": [m.public() for m in self._methods(spec)],
            "busy": state.busy,
            "op_kind": state.op_kind,
            "elapsed_sec": int(time.time() - state.started_at) if state.started_at else 0,
            "error": state.error,
            "log_tail": list(state.log_tail),
            "is_root": _is_root(),
        }

    async def start_install(self, dep_id: str, *, method_index: int = 0) -> dict[str, Any]:
        spec = self._spec(dep_id)
        methods = self._methods(spec)
        if not methods:
            return {"ok": False, "busy": False, "error": "no_install_method"}
        if method_index < 0 or method_index >= len(methods):
            raise ValueError(f"invalid install method index for {dep_id}")
        method = methods[method_index]
        if method.requires_sudo and not _is_root():
            return {"ok": False, "busy": False, "error": "requires_sudo", "method": method.public()}
        if method.command is None:
            return {"ok": False, "busy": False, "error": "manual_install_required"}

        lock = self._locks[dep_id]
        async with lock:
            state = self._state[dep_id]
            if state.busy:
                return {"ok": True, "busy": True, "already_running": True}
            state.busy = True
            state.op_kind = "install"
            state.started_at = time.time()
            state.error = ""
            state.log_tail.clear()
            state.log_tail.append("$ " + " ".join(method.command))
            state.task = asyncio.create_task(self._run_install(dep_id, method.command))
            return {"ok": True, "busy": True}

    async def _run_install(self, dep_id: str, command: tuple[str, ...]) -> None:
        state = self._state[dep_id]
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if dep_id == "playwright-chromium":
            env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_openakita_root() / "ms-playwright"))
        install_ok = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                state.log_tail.append(line.decode("utf-8", errors="replace").rstrip())
            rc = await proc.wait()
            if rc != 0:
                state.error = f"installer exited with code {rc}"
            else:
                install_ok = True
        except Exception as exc:  # noqa: BLE001 - surface in status
            state.error = f"{type(exc).__name__}: {exc}"
        finally:
            if dep_id == "playwright-chromium" and install_ok:
                os.environ.setdefault(
                    "PLAYWRIGHT_BROWSERS_PATH", str(_openakita_root() / "ms-playwright")
                )
            _refresh_process_path_windows()
            state.busy = False
            state.op_kind = ""

    async def aclose(self) -> None:
        for state in self._state.values():
            if state.task is not None and not state.task.done():
                state.task.cancel()

    def _detect(self, spec: DepSpec) -> dict[str, Any]:
        if spec.kind == "playwright":
            return self._detect_playwright(spec)
        return self._detect_binary(spec)

    def _detect_binary(self, spec: DepSpec) -> dict[str, Any]:
        _refresh_process_path_windows()
        location = ""
        for probe in spec.probes:
            location = shutil.which(probe) or _scan_well_known_paths_windows(probe)
            if location:
                break
        version = ""
        if location and spec.version_argv:
            argv = (location, *spec.version_argv[1:])
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    check=False,
                )
                match = re.search(spec.version_regex, proc.stdout or proc.stderr or "")
                if match:
                    version = match.group(1)
            except Exception:
                version = ""
        return self._base_snapshot(spec, bool(location), version, location)

    def _detect_playwright(self, spec: DepSpec) -> dict[str, Any]:
        try:
            import playwright  # type: ignore[import-not-found]
        except Exception as exc:
            snap = self._base_snapshot(spec, False, "", "")
            snap["error"] = f"playwright python package unavailable: {exc}"
            return snap
        roots: list[Path] = []
        env_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
        if env_root:
            roots.append(Path(env_root))
        plugin_root = _openakita_root() / "ms-playwright"
        roots.extend(
            [
                plugin_root,
                Path.home() / "AppData" / "Local" / "ms-playwright",
                Path.home() / ".cache" / "ms-playwright",
            ]
        )
        for root in roots:
            if not root or not root.is_dir():
                continue
            hits = sorted(root.glob("chromium*"))
            if hits:
                if root == plugin_root:
                    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(plugin_root))
                return self._base_snapshot(
                    spec,
                    True,
                    str(getattr(playwright, "__version__", "")),
                    str(hits[-1]),
                )
        return self._base_snapshot(spec, False, str(getattr(playwright, "__version__", "")), "")

    def _base_snapshot(
        self, spec: DepSpec, found: bool, version: str, location: str
    ) -> dict[str, Any]:
        return {
            "id": spec.id,
            "label": spec.display_name,
            "description": spec.description,
            "homepage": spec.homepage,
            "kind": spec.kind,
            "found": found,
            "version": version,
            "location": location,
        }

    def _methods(self, spec: DepSpec) -> list[InstallMethod]:
        if spec.id == "ffprobe":
            return self._methods(self._spec("ffmpeg"))
        return [m for m in spec.install_methods if m.platform == self._platform]

    @staticmethod
    def _spec(dep_id: str) -> DepSpec:
        spec = _SPECS.get(dep_id)
        if spec is None:
            raise ValueError(f"unknown system dependency: {dep_id}")
        return spec
