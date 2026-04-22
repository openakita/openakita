"""Dependency Gate — declarative system-dependency detection + install.

Why
---
Most AI media plugins (FFmpeg-based or whisper.cpp-based) silently break the
moment a system tool is missing. Each plugin used to roll its own
``shutil.which("ffmpeg")`` check and dump installation commands into the UI.
This duplicated logic, leaked secrets in error strings, and gave non-technical
users no path to recovery.

DependencyGate centralises that logic:

- A small **catalog** declares every supported binary (``ffmpeg``,
  ``whisper.cpp``, ``yt-dlp``, …) with platform-specific install methods.
- ``check(dep_id)`` reports the current status (found/missing/version).
- ``install(dep_id)`` is an **async generator** that streams progress events,
  meant to be wrapped in an SSE endpoint by the host.
- Strict allow-listing: only commands declared in the catalog can be invoked.
  Plugins cannot inject arbitrary shell strings, which keeps the ``binary.exec``
  permission unnecessary for the install path.

Usage from a plugin:

    from openakita_plugin_sdk.contrib import DependencyGate, FFMPEG, WHISPER_CPP

    gate = DependencyGate([FFMPEG, WHISPER_CPP])
    status = gate.check("ffmpeg")
    if not status.found:
        async for event in gate.install("ffmpeg"):
            print(event)

The gate itself never calls the host's audit logger — that wiring lives in
``api/routes/plugin_deps.py`` so audit policy stays host-controlled.

Design constraints
------------------
- **Stdlib only.** No httpx / aiosqlite imports here; async runs through
  ``asyncio.create_subprocess_exec``.
- **Idempotent detection.** Calling ``check`` repeatedly is cheap.
- **No silent failures.** Every install method records its return code and the
  caller decides whether to retry / surface to the user.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Platform = Literal["windows", "macos", "linux"]
Strategy = Literal[
    "winget",      # Windows: winget install ...
    "brew",        # macOS: brew install ...
    "apt",         # Debian/Ubuntu: apt install (needs sudo)
    "dnf",         # Fedora/RHEL: dnf install (needs sudo)
    "pip",         # any platform: pip install
    "script",      # arbitrary shell command (must be in catalog whitelist)
    "manual",      # no automated installer; UI must show link / instructions
]


def current_platform() -> Platform:
    sys_name = platform.system().lower()
    if sys_name.startswith("win"):
        return "windows"
    if sys_name == "darwin":
        return "macos"
    return "linux"


@dataclass(frozen=True)
class InstallMethod:
    """A single way to install a dependency on one platform."""

    platform: Platform
    strategy: Strategy
    # ``command`` is split into argv to avoid any shell-injection vector;
    # ``None`` for ``manual`` (UI just shows ``manual_url``).
    command: tuple[str, ...] | None
    description: str = ""
    # ``requires_sudo`` makes the host refuse the call unless the running
    # process can elevate (Linux). Windows winget/macOS brew never need sudo.
    requires_sudo: bool = False
    # ``requires_confirm`` always forces the UI to prompt before running.
    requires_confirm: bool = True
    # For ``manual`` strategy, where to send the user.
    manual_url: str = ""
    # Human-readable estimate displayed before install starts.
    estimated_seconds: int = 60


@dataclass(frozen=True)
class SystemDependency:
    """Declarative description of one system dependency."""

    id: str
    display_name: str
    description: str
    # Binary name(s) to search on PATH. The first one found counts as "present".
    probes: tuple[str, ...]
    # Per-platform install methods. A platform with no entry shows ``manual``
    # fallback (UI displays homepage link only).
    install_methods: tuple[InstallMethod, ...] = ()
    # Optional argv to query the version. ``stdout.decode()`` is parsed by
    # ``version_regex`` (first capture group).
    version_argv: tuple[str, ...] = ()
    version_regex: str = r"(\d+\.\d+(?:\.\d+)?)"
    # Homepage shown in any UI fallback / "learn more" link.
    homepage: str = ""


@dataclass
class DepStatus:
    """Detected state of one dependency on this machine."""

    id: str
    found: bool
    version: str = ""
    location: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "found": self.found,
            "version": self.version,
            "location": self.location,
            "error": self.error,
        }


@dataclass
class InstallEvent:
    """One progress event emitted by ``DependencyGate.install``."""

    phase: Literal["start", "stdout", "stderr", "exit", "done", "error", "skip"]
    dep_id: str
    line: str = ""
    return_code: int | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "phase": self.phase,
            "dep_id": self.dep_id,
        }
        if self.line:
            out["line"] = self.line
        if self.return_code is not None:
            out["return_code"] = self.return_code
        if self.extra:
            out["extra"] = self.extra
        return out


class DependencyGate:
    """Detect and (optionally) install whitelisted system dependencies.

    Construct with the catalog entries the plugin (or host route) is willing
    to manage. Checking and installing dependencies not in the catalog raises
    ``KeyError`` — this is intentional, the gate is the security boundary.
    """

    def __init__(self, catalog: Sequence[SystemDependency]) -> None:
        self._catalog: dict[str, SystemDependency] = {dep.id: dep for dep in catalog}
        self._cache: dict[str, DepStatus] = {}

    @property
    def catalog(self) -> dict[str, SystemDependency]:
        """Read-only view of the registered dependencies."""
        return dict(self._catalog)

    def known(self, dep_id: str) -> bool:
        return dep_id in self._catalog

    # ── Detection ───────────────────────────────────────────────────────

    def check(self, dep_id: str, *, force: bool = False) -> DepStatus:
        """Return current status. Cached; pass ``force=True`` to re-probe."""
        if dep_id not in self._catalog:
            raise KeyError(f"Unknown dependency '{dep_id}'")
        if not force and dep_id in self._cache:
            return self._cache[dep_id]

        dep = self._catalog[dep_id]
        location = ""
        for probe in dep.probes:
            found_path = shutil.which(probe)
            if found_path:
                location = found_path
                break

        if not location:
            status = DepStatus(id=dep_id, found=False)
            self._cache[dep_id] = status
            return status

        version = ""
        if dep.version_argv:
            version = self._safe_version_query(dep, location)
        status = DepStatus(id=dep_id, found=True, location=location, version=version)
        self._cache[dep_id] = status
        return status

    def check_all(self, dep_ids: Sequence[str] | None = None) -> dict[str, DepStatus]:
        targets = list(dep_ids) if dep_ids else list(self._catalog.keys())
        return {dep_id: self.check(dep_id) for dep_id in targets}

    def invalidate(self, dep_id: str | None = None) -> None:
        """Drop cached detection results (call after install)."""
        if dep_id is None:
            self._cache.clear()
        else:
            self._cache.pop(dep_id, None)

    @staticmethod
    def _safe_version_query(dep: SystemDependency, location: str) -> str:
        """Run the version probe with a tight timeout. Best-effort only.

        ``version_argv`` is declared as if the binary were on PATH (e.g.
        ``("ffmpeg", "-version")``); we substitute the resolved ``location``
        as argv[0] so we always invoke the *detected* executable instead of
        whichever PATH lookup ``shutil.which`` happens to do next.
        """
        import subprocess

        try:
            argv: tuple[str, ...] = (location,) + tuple(dep.version_argv[1:])
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
            match = re.search(dep.version_regex, blob)
            return match.group(1) if match else ""
        except Exception as exc:
            logger.debug("Version probe for %s failed: %s", dep.id, exc)
            return ""

    # ── Install methods ────────────────────────────────────────────────

    def list_install_methods(self, dep_id: str) -> list[InstallMethod]:
        """Methods that apply to the current OS, ordered by preference."""
        if dep_id not in self._catalog:
            raise KeyError(f"Unknown dependency '{dep_id}'")
        target = current_platform()
        return [m for m in self._catalog[dep_id].install_methods if m.platform == target]

    async def install(
        self,
        dep_id: str,
        *,
        method_index: int = 0,
        env_overrides: dict[str, str] | None = None,
    ) -> AsyncIterator[InstallEvent]:
        """Stream install events for one dependency.

        The host is responsible for permission checks and audit logging.
        """
        if dep_id not in self._catalog:
            raise KeyError(f"Unknown dependency '{dep_id}'")

        methods = self.list_install_methods(dep_id)
        if not methods:
            yield InstallEvent(
                phase="skip",
                dep_id=dep_id,
                line=f"No automated installer for {dep_id} on {current_platform()}",
                extra={"reason": "no_method", "homepage": self._catalog[dep_id].homepage},
            )
            return

        if method_index < 0 or method_index >= len(methods):
            yield InstallEvent(
                phase="error",
                dep_id=dep_id,
                line=f"method_index {method_index} out of range (have {len(methods)})",
            )
            return

        method = methods[method_index]
        if method.strategy == "manual" or method.command is None:
            yield InstallEvent(
                phase="skip",
                dep_id=dep_id,
                line=f"Manual install required for {dep_id}",
                extra={"reason": "manual", "manual_url": method.manual_url},
            )
            return

        argv = list(method.command)
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)

        yield InstallEvent(
            phase="start",
            dep_id=dep_id,
            line=" ".join(argv),
            extra={
                "strategy": method.strategy,
                "platform": method.platform,
                "estimated_seconds": str(method.estimated_seconds),
            },
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            yield InstallEvent(
                phase="error",
                dep_id=dep_id,
                line=f"installer not found: {exc}",
            )
            return
        except Exception as exc:
            yield InstallEvent(
                phase="error",
                dep_id=dep_id,
                line=f"failed to spawn installer: {exc}",
            )
            return

        async def _drain(reader: asyncio.StreamReader | None, phase: str) -> AsyncIterator[InstallEvent]:
            if reader is None:
                return
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                try:
                    text = raw.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    text = repr(raw)
                if not text:
                    continue
                yield InstallEvent(phase=phase, dep_id=dep_id, line=text)  # type: ignore[arg-type]

        async def _merge() -> AsyncIterator[InstallEvent]:
            queue: asyncio.Queue[InstallEvent | None] = asyncio.Queue()

            async def pump(stream: asyncio.StreamReader | None, phase: str) -> None:
                async for ev in _drain(stream, phase):
                    await queue.put(ev)
                await queue.put(None)

            tasks = [
                asyncio.create_task(pump(proc.stdout, "stdout")),
                asyncio.create_task(pump(proc.stderr, "stderr")),
            ]
            done_count = 0
            try:
                while done_count < 2:
                    item = await queue.get()
                    if item is None:
                        done_count += 1
                        continue
                    yield item
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()

        async for ev in _merge():
            yield ev

        rc = await proc.wait()
        yield InstallEvent(phase="exit", dep_id=dep_id, return_code=rc)

        # Re-detect so the cache reflects post-install state and downstream
        # callers do not need to remember to invalidate.
        self.invalidate(dep_id)
        new_status = self.check(dep_id, force=True)
        if new_status.found:
            yield InstallEvent(
                phase="done",
                dep_id=dep_id,
                return_code=rc,
                extra={
                    "found": "true",
                    "version": new_status.version,
                    "location": new_status.location,
                },
            )
        else:
            yield InstallEvent(
                phase="error",
                dep_id=dep_id,
                return_code=rc,
                line=f"Installer exited rc={rc} but {dep_id} still not found on PATH",
            )


__all__ = [
    "DependencyGate",
    "DepStatus",
    "InstallEvent",
    "InstallMethod",
    "Platform",
    "Strategy",
    "SystemDependency",
    "current_platform",
]
