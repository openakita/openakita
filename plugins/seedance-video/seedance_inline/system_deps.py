"""Self-contained system dependency detector + fire-and-poll installer.

After SDK 0.7.0 retired ``openakita_plugin_sdk.contrib.DependencyGate`` and
the host-mounted ``/api/plugins/_sdk/deps/*`` SSE endpoint, plugins that
need a system binary (here: FFmpeg) must own their installer end-to-end.

Design rules
------------
- **White-listed argv only.** Install commands are hard-coded per platform;
  no ``shell=True``, no user-supplied tokens get spliced into argv.
- **Single dep installs serialise** behind one ``asyncio.Lock`` so a runaway
  client cannot fork N parallel ``winget`` invocations.
- **Fire-and-poll**, not SSE. ``start_install`` returns immediately; the
  frontend polls ``status`` every few seconds. The last 50 lines of stdout
  + stderr are kept in a ``deque`` so the UI can show an "Install log"
  panel without us having to keep an open connection.
- **Never raises** in the public API except for invalid arguments — every
  failure is folded into the returned dict's ``error`` field so callers do
  not need defensive try/except blocks.
- **Stdlib only**: ``asyncio``, ``shutil``, ``subprocess``, ``collections``,
  ``platform``, ``os``.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

Platform = Literal["windows", "macos", "linux"]


def _current_platform() -> Platform:
    name = platform.system().lower()
    if name.startswith("win"):
        return "windows"
    if name == "darwin":
        return "macos"
    return "linux"


def _is_root() -> bool:
    """POSIX-only effective root check; Windows always returns False."""
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return False


# ── Windows PATH refresh ──────────────────────────────────────────────────
#
# winget / choco / msi installers update PATH in the Windows registry, but
# the change is only inherited by NEW processes — the running OpenAkita
# process keeps its boot-time os.environ["PATH"], so shutil.which() will
# never see the freshly installed binary until the server is restarted.
#
# We mitigate this by re-reading the User + System PATH from the registry
# after a Windows install (and on every detect() call as a cheap safety
# net), merging anything new into os.environ["PATH"] for the current
# process. This is purely additive — we never shrink PATH.

def _read_registry_path_windows() -> str:
    """Concatenate HKLM\\System Env Path + HKCU\\Environment Path.

    Returns "" on non-Windows or if the registry is unreadable. Never raises.
    """
    if os.name != "nt":
        return ""
    try:
        import winreg  # stdlib on Windows
    except Exception:
        return ""

    parts: list[str] = []
    sources = (
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    )
    for root, sub in sources:
        try:
            with winreg.OpenKey(root, sub) as k:
                val, _ = winreg.QueryValueEx(k, "Path")
                if val:
                    parts.append(str(val))
        except OSError:
            continue
        except Exception as exc:
            logger.debug("registry PATH read failed for %s: %s", sub, exc)
    return os.pathsep.join(parts)


def _refresh_process_path_windows() -> bool:
    """Merge registry PATH into os.environ["PATH"] (Windows only).

    Returns True if any new entries were added. Safe to call repeatedly.
    """
    if os.name != "nt":
        return False
    extra = _read_registry_path_windows()
    if not extra:
        return False
    current = os.environ.get("PATH", "")
    seen = {p.strip().lower() for p in current.split(os.pathsep) if p.strip()}
    added: list[str] = []
    for entry in extra.split(os.pathsep):
        e = entry.strip()
        if not e:
            continue
        # Expand %VARS% the registry leaves untouched (REG_EXPAND_SZ).
        if "%" in e:
            e = os.path.expandvars(e)
        if e.lower() not in seen:
            added.append(e)
            seen.add(e.lower())
    if not added:
        return False
    os.environ["PATH"] = current + (os.pathsep if current else "") + os.pathsep.join(added)
    logger.info("Refreshed PATH from registry: +%d entries", len(added))
    return True


# Well-known fallback locations for binaries that some installers (notably
# winget's Gyan.FFmpeg) place under the per-user app data tree without
# putting the bin dir on PATH directly — they only register a shim under
# Microsoft\WinGet\Links. If even the shim is missing from the in-process
# PATH, fall back to globbing these spots.
_WELL_KNOWN_BIN_GLOBS: dict[str, list[str]] = {
    "ffmpeg": [
        # winget Gyan.FFmpeg
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffmpeg.exe",
        r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe",
        # choco
        r"%PROGRAMDATA%\chocolatey\bin\ffmpeg.exe",
        # scoop (per-user)
        r"%USERPROFILE%\scoop\shims\ffmpeg.exe",
        # common manual installs
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"%PROGRAMFILES%\ffmpeg\bin\ffmpeg.exe",
    ],
}


def _scan_well_known_paths_windows(probe: str) -> str:
    """Return absolute path to ``probe`` if found in any well-known spot."""
    if os.name != "nt":
        return ""
    patterns = _WELL_KNOWN_BIN_GLOBS.get(probe, [])
    for pattern in patterns:
        expanded = os.path.expandvars(pattern)
        try:
            for hit in glob.glob(expanded, recursive=True):
                if os.path.isfile(hit):
                    return hit
        except Exception as exc:
            logger.debug("well-known glob failed for %s: %s", pattern, exc)
    return ""


@dataclass(frozen=True)
class InstallMethod:
    """One platform-specific install recipe."""

    platform: Platform
    strategy: str          # "winget" | "brew" | "apt" | "dnf" | "manual"
    command: tuple[str, ...] | None
    description: str
    requires_sudo: bool = False
    estimated_seconds: int = 120
    manual_url: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        """Public-safe view (argv hidden — only the strategy + command_hint)."""
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
    """Declarative spec for one system dependency."""

    id: str
    display_name: str
    description: str
    homepage: str
    probes: tuple[str, ...]
    version_argv: tuple[str, ...]
    version_regex: str
    install_methods: tuple[InstallMethod, ...]
    # Uninstall recipes mirror install: same per-platform whitelisted argv,
    # same requires_sudo gating. ``manual`` strategies are skipped because
    # there is nothing to spawn — the UI just hides the button instead.
    uninstall_methods: tuple[InstallMethod, ...] = ()


# ── Catalog (keep tiny; only what seedance-video itself needs) ────────────

_FFMPEG = DepSpec(
    id="ffmpeg",
    display_name="FFmpeg",
    description=(
        "Video / audio toolkit used for long-video stitching in the "
        "Storyboard tab and for video thumbnail previews in the Asset library."
    ),
    homepage="https://ffmpeg.org/download.html",
    probes=("ffmpeg",),
    version_argv=("ffmpeg", "-version"),
    version_regex=r"ffmpeg version\s+(\S+)",
    install_methods=(
        InstallMethod(
            platform="windows",
            strategy="winget",
            command=(
                "winget", "install", "--id", "Gyan.FFmpeg",
                "-e", "--accept-source-agreements", "--accept-package-agreements",
            ),
            description="Install FFmpeg via Windows Package Manager (winget).",
            requires_sudo=False,
            estimated_seconds=120,
        ),
        InstallMethod(
            platform="macos",
            strategy="brew",
            command=("brew", "install", "ffmpeg"),
            description="Install FFmpeg via Homebrew.",
            requires_sudo=False,
            estimated_seconds=180,
        ),
        InstallMethod(
            platform="linux",
            strategy="apt",
            command=("apt-get", "install", "-y", "ffmpeg"),
            description="Install FFmpeg via apt (Debian / Ubuntu). Requires root.",
            requires_sudo=True,
            estimated_seconds=120,
        ),
        InstallMethod(
            platform="linux",
            strategy="dnf",
            command=("dnf", "install", "-y", "ffmpeg"),
            description="Install FFmpeg via dnf (Fedora / RHEL). Requires root.",
            requires_sudo=True,
            estimated_seconds=120,
        ),
        InstallMethod(
            platform="linux",
            strategy="manual",
            command=None,
            description="No package manager available — download a static build.",
            manual_url="https://johnvansickle.com/ffmpeg/",
        ),
    ),
    uninstall_methods=(
        InstallMethod(
            platform="windows",
            strategy="winget",
            # ``--accept-source-agreements`` mirrors the install command:
            # without it, winget on a freshly-onboarded box can return
            # exit code 0 without actually uninstalling anything (it
            # silently aborts on the EULA prompt because ``--silent``
            # suppresses output but does not auto-accept agreements).
            # User then sees "卸载失败：已安装" because our detect()
            # still finds the binary.
            command=(
                "winget", "uninstall", "--id", "Gyan.FFmpeg",
                "-e", "--silent", "--accept-source-agreements",
            ),
            description="Uninstall FFmpeg via Windows Package Manager (winget).",
            requires_sudo=False,
            estimated_seconds=30,
        ),
        InstallMethod(
            platform="macos",
            strategy="brew",
            command=("brew", "uninstall", "ffmpeg"),
            description="Uninstall FFmpeg via Homebrew.",
            requires_sudo=False,
            estimated_seconds=30,
        ),
        InstallMethod(
            platform="linux",
            strategy="apt",
            command=("apt-get", "remove", "-y", "ffmpeg"),
            description="Remove FFmpeg via apt (Debian / Ubuntu). Requires root.",
            requires_sudo=True,
            estimated_seconds=30,
        ),
        InstallMethod(
            platform="linux",
            strategy="dnf",
            command=("dnf", "remove", "-y", "ffmpeg"),
            description="Remove FFmpeg via dnf (Fedora / RHEL). Requires root.",
            requires_sudo=True,
            estimated_seconds=30,
        ),
    ),
)


_SPECS: dict[str, DepSpec] = {_FFMPEG.id: _FFMPEG}


@dataclass
class _RunState:
    """Mutable per-dep runtime state (not exposed directly).

    A single _RunState tracks whichever operation (install or uninstall) is
    currently or most-recently active — the ``op_kind`` field disambiguates
    so the UI can label the badge / log panel correctly. Only one operation
    can run per dep at a time (enforced by the per-dep lock).
    """

    found: bool = False
    version: str = ""
    location: str = ""
    detect_error: str = ""

    busy: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0
    return_code: int | None = None
    method_strategy: str = ""
    op_kind: str = ""              # "install" | "uninstall" | ""
    install_error: str = ""        # historical name; reused for uninstall errors
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=50))
    _proc: asyncio.subprocess.Process | None = None
    _drain_task: asyncio.Task[Any] | None = None


class SystemDepsManager:
    """Detect + install/uninstall whitelisted system binaries.

    Methods exposed to plugin routes (all return plain dicts):

    - :meth:`list_components` — full snapshot for the Settings page
    - :meth:`detect` — re-probe one dep
    - :meth:`methods` / :meth:`uninstall_methods` — recipes that apply to
      the current OS (only ones whose package manager is on PATH)
    - :meth:`start_install` / :meth:`start_uninstall` — kick off the op
      (idempotent if already busy; only one op per dep at a time)
    - :meth:`status` — live status snapshot during/after the last op
    - :meth:`aclose` — cancel any in-flight drain task on plugin unload
    """

    def __init__(self) -> None:
        self._state: dict[str, _RunState] = {dep_id: _RunState() for dep_id in _SPECS}
        self._locks: dict[str, asyncio.Lock] = {dep_id: asyncio.Lock() for dep_id in _SPECS}
        self._platform: Platform = _current_platform()

    # ── Detection ──────────────────────────────────────────────────────

    def detect(self, dep_id: str, *, force: bool = True) -> dict[str, Any]:
        spec = self._require(dep_id)
        st = self._state[dep_id]
        if not force and st.location:
            return self._detect_dict(spec, st)

        st.detect_error = ""
        location = self._probe_locations(spec)

        if not location and self._platform == "windows":
            # Likely just installed via winget — registry got the new PATH
            # but our in-process os.environ did not. Refresh and retry.
            if _refresh_process_path_windows():
                location = self._probe_locations(spec)
            if not location:
                # Last-resort: scan well-known install locations directly,
                # so even a totally PATH-less install (rare but happens
                # with portable extracts) still surfaces as "found".
                for probe in spec.probes:
                    hit = _scan_well_known_paths_windows(probe)
                    if hit:
                        location = hit
                        # Make subsequent shutil.which() calls in this
                        # process find it without another registry sweep.
                        bin_dir = os.path.dirname(hit)
                        if bin_dir and bin_dir.lower() not in {
                            p.strip().lower()
                            for p in os.environ.get("PATH", "").split(os.pathsep)
                            if p.strip()
                        }:
                            os.environ["PATH"] = (
                                os.environ.get("PATH", "")
                                + (os.pathsep if os.environ.get("PATH") else "")
                                + bin_dir
                            )
                        break

        if not location:
            st.found = False
            st.location = ""
            st.version = ""
            return self._detect_dict(spec, st)

        st.found = True
        st.location = location
        st.version = self._safe_version(spec, location)
        return self._detect_dict(spec, st)

    def _probe_locations(self, spec: DepSpec) -> str:
        for probe in spec.probes:
            found_path = shutil.which(probe)
            if found_path:
                return found_path
        return ""

    @staticmethod
    def _detect_dict(spec: DepSpec, st: _RunState) -> dict[str, Any]:
        return {
            "id": spec.id,
            "display_name": spec.display_name,
            "homepage": spec.homepage,
            "found": st.found,
            "version": st.version,
            "location": st.location,
            "error": st.detect_error,
        }

    @staticmethod
    def _safe_version(spec: DepSpec, location: str) -> str:
        if not spec.version_argv:
            return ""
        argv = (location,) + tuple(spec.version_argv[1:])
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=5, check=False,
            )
        except Exception as exc:
            logger.debug("Version probe for %s failed: %s", spec.id, exc)
            return ""
        blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
        match = re.search(spec.version_regex, blob)
        return match.group(1) if match else ""

    # ── Methods listing ────────────────────────────────────────────────

    def methods(self, dep_id: str) -> list[dict[str, Any]]:
        return self._methods_public(dep_id, "install")

    def uninstall_methods(self, dep_id: str) -> list[dict[str, Any]]:
        return self._methods_public(dep_id, "uninstall")

    def _methods_public(self, dep_id: str, op_kind: str) -> list[dict[str, Any]]:
        spec = self._require(dep_id)
        source = self._methods_for(spec, op_kind)
        out: list[dict[str, Any]] = []
        for m in source:
            if m.platform != self._platform:
                continue
            # Hide manager-based methods whose binary is not even on PATH
            # (e.g. ``apt-get`` on a Fedora box). ``manual`` always shows.
            if m.command and m.strategy != "manual":
                if not shutil.which(m.command[0]):
                    continue
            out.append(m.to_public_dict())
        return out

    @staticmethod
    def _methods_for(spec: DepSpec, op_kind: str) -> tuple[InstallMethod, ...]:
        if op_kind == "uninstall":
            return spec.uninstall_methods
        return spec.install_methods

    # ── Aggregate snapshot ─────────────────────────────────────────────

    def list_components(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for dep_id in _SPECS:
            self.detect(dep_id, force=True)
            spec = _SPECS[dep_id]
            st = self._state[dep_id]
            items.append({
                **self._detect_dict(spec, st),
                "description": spec.description,
                "platform": self._platform,
                "is_root": _is_root(),
                "methods": self.methods(dep_id),
                "uninstall_methods": self.uninstall_methods(dep_id),
                "busy": st.busy,
                "last_install": self._install_dict(st),
            })
        return items

    # ── Installation / Uninstallation ──────────────────────────────────

    async def start_install(
        self,
        dep_id: str,
        *,
        method_index: int = 0,
    ) -> dict[str, Any]:
        return await self._start_op(dep_id, "install", method_index)

    async def start_uninstall(
        self,
        dep_id: str,
        *,
        method_index: int = 0,
    ) -> dict[str, Any]:
        return await self._start_op(dep_id, "uninstall", method_index)

    async def _start_op(
        self,
        dep_id: str,
        op_kind: str,
        method_index: int,
    ) -> dict[str, Any]:
        if op_kind not in ("install", "uninstall"):
            return {"ok": False, "busy": False, "error": f"invalid op_kind: {op_kind!r}"}

        spec = self._require(dep_id)
        st = self._state[dep_id]
        lock = self._locks[dep_id]

        if st.busy or lock.locked():
            return {
                "ok": True,
                "busy": True,
                "started_at": st.started_at,
                "method_strategy": st.method_strategy,
                "op_kind": st.op_kind,
                "note": f"{st.op_kind or 'op'}_already_running",
            }

        if op_kind == "uninstall" and not self._state[dep_id].found:
            # Re-probe in case stale state lied — saves the user a confusing
            # "uninstall succeeded but binary still detected" round-trip.
            self.detect(dep_id, force=True)
            if not self._state[dep_id].found:
                return {
                    "ok": False,
                    "busy": False,
                    "error": "not_installed",
                }

        methods = self._methods_public(dep_id, op_kind)
        if not methods:
            verb = "uninstaller" if op_kind == "uninstall" else "installer"
            return {
                "ok": False,
                "busy": False,
                "error": f"no automated {verb} available on {self._platform}",
            }
        if method_index < 0 or method_index >= len(methods):
            return {
                "ok": False,
                "busy": False,
                "error": f"method_index {method_index} out of range (have {len(methods)})",
            }

        chosen_public = methods[method_index]
        method = self._resolve_method(spec, chosen_public, op_kind)
        if method is None or method.command is None:
            return {
                "ok": False,
                "busy": False,
                "error": f"method '{chosen_public.get('strategy')}' has no executable command",
                "manual_url": chosen_public.get("manual_url", ""),
            }

        if method.requires_sudo and not _is_root():
            return {
                "ok": False,
                "busy": False,
                "error": "requires_sudo",
                "command_hint": " ".join(method.command),
            }

        st.busy = True
        st.op_kind = op_kind
        st.started_at = time.time()
        st.finished_at = 0.0
        st.return_code = None
        st.install_error = ""
        st.method_strategy = method.strategy
        st.log_tail.clear()
        st.log_tail.append(f"$ {' '.join(method.command)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *method.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            st.busy = False
            st.finished_at = time.time()
            st.install_error = f"{op_kind}er binary not found: {exc}"
            st.log_tail.append(st.install_error)
            return {"ok": False, "busy": False, "error": st.install_error}
        except Exception as exc:
            st.busy = False
            st.finished_at = time.time()
            st.install_error = f"failed to spawn {op_kind}er: {exc}"
            st.log_tail.append(st.install_error)
            return {"ok": False, "busy": False, "error": st.install_error}

        st._proc = proc
        st._drain_task = asyncio.create_task(
            self._drain_and_finalize(dep_id, proc, lock, op_kind),
            name=f"sysdeps:{op_kind}:{dep_id}",
        )

        return {
            "ok": True,
            "busy": True,
            "started_at": st.started_at,
            "method_strategy": st.method_strategy,
            "op_kind": op_kind,
            "estimated_seconds": method.estimated_seconds,
        }

    async def _drain_and_finalize(
        self,
        dep_id: str,
        proc: asyncio.subprocess.Process,
        lock: asyncio.Lock,
        op_kind: str,
    ) -> None:
        st = self._state[dep_id]
        async with lock:
            try:
                async def pump(reader: asyncio.StreamReader | None, _label: str) -> None:
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
                        if text:
                            st.log_tail.append(text)

                await asyncio.gather(
                    pump(proc.stdout, "stdout"),
                    pump(proc.stderr, "stderr"),
                )
                rc = await proc.wait()
                st.return_code = rc
                if rc != 0:
                    st.install_error = f"{op_kind}er exited with code {rc}"
            except asyncio.CancelledError:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                st.install_error = f"{op_kind} cancelled (plugin unloading)"
                raise
            except Exception as exc:
                logger.exception("%s drain crashed for %s", op_kind, dep_id)
                st.install_error = f"internal_error: {exc}"
            finally:
                st.finished_at = time.time()
                st.busy = False
                st._proc = None
                # Mirror the post-install PATH refresh for uninstall too:
                # winget uninstall removes the PATH entry from the registry
                # but the running process still has it cached — without a
                # refresh shutil.which() would keep "finding" a now-broken
                # binary path and the UI would lie about the new state.
                if self._platform == "windows":
                    try:
                        _refresh_process_path_windows()
                    except Exception as exc:
                        logger.debug("post-%s PATH refresh failed: %s", op_kind, exc)

                # ── Post-op detect with bounded retry ─────────────────
                # Two real-world races we have to absorb:
                #
                # 1) Post-INSTALL on Windows: winget exits rc=0 the
                #    moment the MSI/portable extract finishes, but the
                #    registry PATH update is sometimes still propagating
                #    when our finally block runs.  A single detect() call
                #    then misses the binary and the UI reports
                #    "install failed" even though it actually succeeded.
                #
                # 2) Post-UNINSTALL on Windows: winget unlinks files
                #    asynchronously; an immediate detect() can still
                #    glob a half-deleted package directory and report
                #    "uninstall failed: still installed".
                #
                # We retry detect a few times with short backoff.  Total
                # wait is capped at ~2.5s so the UI does not stall.
                expected_found = (op_kind == "install")
                for attempt in range(5):
                    try:
                        self.detect(dep_id, force=True)
                    except Exception as exc:
                        logger.debug(
                            "post-%s detect attempt %d failed: %s",
                            op_kind, attempt, exc,
                        )
                        break
                    if st.found == expected_found:
                        break
                    # On uninstall, also wipe the cached location so the
                    # next detect cannot trust a stale glob hit.
                    if op_kind == "uninstall":
                        st.location = ""
                    await asyncio.sleep(0.5)
                logger.info(
                    "post-%s detect: found=%s version=%s location=%s rc=%s",
                    op_kind, st.found, st.version, st.location, st.return_code,
                )

    # ── Status ─────────────────────────────────────────────────────────

    def status(self, dep_id: str) -> dict[str, Any]:
        spec = self._require(dep_id)
        st = self._state[dep_id]
        return {
            "ok": True,
            "id": spec.id,
            **self._install_dict(st),
            "found": st.found,
            "version": st.version,
            "location": st.location,
        }

    @staticmethod
    def _install_dict(st: _RunState) -> dict[str, Any]:
        elapsed = 0.0
        if st.started_at:
            end = st.finished_at if st.finished_at else time.time()
            elapsed = max(0.0, end - st.started_at)
        return {
            "busy": st.busy,
            "op_kind": st.op_kind,
            "started_at": st.started_at,
            "finished_at": st.finished_at,
            "elapsed_sec": round(elapsed, 1),
            "return_code": st.return_code,
            "method_strategy": st.method_strategy,
            "error": st.install_error,
            "log_tail": list(st.log_tail),
        }

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Cancel any in-flight install drain (plugin unload)."""
        for st in self._state.values():
            task = st._drain_task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # ── Helpers ────────────────────────────────────────────────────────

    def _require(self, dep_id: str) -> DepSpec:
        spec = _SPECS.get(dep_id)
        if spec is None:
            raise ValueError(f"unknown dep_id: {dep_id!r}")
        return spec

    def _resolve_method(
        self, spec: DepSpec, public: dict[str, Any], op_kind: str = "install",
    ) -> InstallMethod | None:
        """Match a public method-dict back to the original ``InstallMethod``.

        We use ``(platform, strategy)`` as the natural key — combinations are
        unique within a single ``DepSpec`` (per op_kind).
        """
        target_strategy = public.get("strategy")
        for m in self._methods_for(spec, op_kind):
            if m.platform == self._platform and m.strategy == target_strategy:
                return m
        return None


__all__ = [
    "DepSpec",
    "InstallMethod",
    "SystemDepsManager",
]
