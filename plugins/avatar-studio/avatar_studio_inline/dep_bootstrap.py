"""Self-sufficient runtime dependency bootstrap for avatar-studio.

Why this exists
---------------

The OpenAkita Desktop binary (PyInstaller-frozen ``openakita-server.exe``)
**does not always call** ``runtime_env.inject_module_paths()`` — that
helper only runs from the ``python -m openakita`` entry path and the
setup-center script. The frozen server binary takes a different entry
path, so any wheels we drop into ``~/.openakita/modules/<id>/site-packages/``
or any deps the host installs into ``<plugin_dir>/deps/`` via
``install_pip_deps`` may sit there **un-imported**.

Result before this module existed: every fresh OpenAkita install would
hit ``OSS 上传失败：oss2 SDK not installed`` and the user had to drop
to a shell, find the right interpreter (which is *not* what
``sys.executable`` reports — that's the openakita-server.exe wrapper),
and ``pip install oss2`` themselves.

What this module does
---------------------

1. **Probe** the three places host-managed deps could already live and
   append them to ``sys.path`` if missing:

   - ``<plugin_dir>/deps/`` — populated by host's ``install_pip_deps``
     from ``plugin.json`` ``requires.pip``.
   - ``~/.openakita/modules/avatar-studio/site-packages/`` — host's
     "optional modules" location.
   - ``<sys.executable>/_internal/Lib/site-packages/`` — bundled
     interpreter's own site-packages (mostly read-only on Windows but
     occasionally seeded by older OpenAkita versions).

2. **Import-or-install**: if the import still fails, run ``pip install``
   in-process against the resolved real Python (walks past the
   PyInstaller wrapper to find ``_internal/python.exe``), targeting
   ``~/.openakita/modules/avatar-studio/site-packages/`` so the install
   is private to this plugin and survives plugin reinstalls.

3. **Mirror-aware**: tries Aliyun → Tsinghua → USTC → official PyPI in
   sequence so users behind the GFW don't time out on pypi.org.

Public API
----------

- ``ensure_importable(import_name, pip_spec, *, friendly_name=None)``
  → ``ModuleType``. Raises ``DepInstallFailed`` if every fallback was
  exhausted. The error wraps the underlying pip / network error so
  callers can surface a single clean message in the UI.

- ``preinstall_async(specs)`` — fire-and-forget background install
  triggered from ``plugin.on_load`` so the cost is paid during app
  startup, not on the user's first upload click.
"""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level lock so two concurrent uploads don't try to pip-install
# the same package twice. We deliberately use a re-entrant lock because
# ``preinstall_async`` may invoke the same code path that a synchronous
# ``ensure_importable`` later joins on.
_INSTALL_LOCK = threading.RLock()

# Cache of "this dep is now importable" so we don't re-probe every call.
_RESOLVED: set[str] = set()
_DEP_STATE: dict[str, dict[str, Any]] = {}
_DEP_LOGS: dict[str, deque[str]] = {}


class DepInstallFailed(Exception):
    """Raised when neither sys.path probing nor pip install made the dep importable."""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause


def _log_dep(dep_id: str, message: str) -> None:
    tail = _DEP_LOGS.setdefault(dep_id, deque(maxlen=80))
    tail.append(f"{time.strftime('%H:%M:%S')} {message}")


def _state_for(dep_id: str) -> dict[str, Any]:
    state = _DEP_STATE.setdefault(
        dep_id,
        {
            "busy": False,
            "last_error": "",
            "last_started_at": 0.0,
            "last_finished_at": 0.0,
            "last_success": False,
        },
    )
    state["log_tail"] = list(_DEP_LOGS.get(dep_id, ()))
    return dict(state)


def _patch_simplejson_jsondecodeerror() -> bool:
    """Patch broken bundled ``simplejson`` so ``requests`` can import.

    Some PyInstaller OpenAkita builds expose a ``simplejson`` module that
    lacks ``JSONDecodeError``. ``requests`` prefers ``simplejson`` when it is
    importable and then does ``from simplejson import JSONDecodeError``.
    ``oss2`` imports ``requests``, so the missing attribute shows up as an
    ``ImportError`` while importing ``oss2`` even when ``oss2`` is already
    installed correctly. Without this patch the bootstrapper mistakes that
    compatibility issue for "package missing" and unnecessarily invokes pip.
    """
    try:
        import simplejson as _sj
    except Exception:
        return False
    if hasattr(_sj, "JSONDecodeError"):
        return False
    try:
        from json.decoder import JSONDecodeError as _JSONDecodeError
    except Exception:

        class _JSONDecodeError(ValueError):
            pass

    _sj.JSONDecodeError = _JSONDecodeError
    errors_mod = getattr(_sj, "errors", None)
    if errors_mod is not None and not hasattr(errors_mod, "JSONDecodeError"):
        try:
            errors_mod.JSONDecodeError = _JSONDecodeError
        except Exception:
            pass
    logger.info("avatar-studio: patched simplejson.JSONDecodeError for requests")
    return True


# ── path resolution ─────────────────────────────────────────────────


def _openakita_root() -> Path:
    """Mirror ``runtime_env._get_openakita_root`` without importing it.

    The plugin must NOT depend on ``openakita.*`` internals (those move
    between releases) so we re-derive the same value: ``$OPENAKITA_ROOT``
    if set, else ``~/.openakita``.
    """
    env_root = os.environ.get("OPENAKITA_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    return Path.home() / ".openakita"


def _candidate_site_dirs(plugin_dir: Path | None) -> list[Path]:
    """All directories that *might* already contain installed wheels."""
    cands: list[Path] = []
    if plugin_dir is not None:
        cands.append(plugin_dir / "deps")
    # Prefer the current clean target. The legacy ``site-packages`` target may
    # contain a half-written wheel if an earlier pip --upgrade was interrupted
    # while the OpenAkita process still had a .pyd file locked (seen with
    # crcmod). Keeping a new target lets us recover without asking the user to
    # close the app and delete locked files by hand.
    cands.append(_install_target())
    cands.append(_openakita_root() / "modules" / "avatar-studio" / "site-packages")
    # _internal/Lib/site-packages of the PyInstaller-bundled Python —
    # not always writable but sometimes already seeded.
    exe_dir = Path(sys.executable).parent
    internal = exe_dir if exe_dir.name == "_internal" else exe_dir / "_internal"
    if internal.is_dir():
        cands.append(internal / "Lib" / "site-packages")
    return cands


def _install_target() -> Path:
    """Writable clean target for auto-installed plugin deps."""
    return (
        _openakita_root()
        / "modules"
        / "avatar-studio"
        / f"site-packages-py{sys.version_info.major}{sys.version_info.minor}-runtime"
    )


def _ensure_on_syspath(plugin_dir: Path | None) -> list[str]:
    """Append every existing candidate to ``sys.path`` (idempotent).

    Returns the list of paths actually appended this call (for logging).
    Append (not insert) keeps PyInstaller's bundled stdlib at the front
    so a plugin-local copy of e.g. ``pydantic`` won't override the
    host's — same precaution as ``runtime_env.inject_module_paths``.
    """
    appended: list[str] = []
    for cand in _candidate_site_dirs(plugin_dir):
        if not cand.is_dir():
            continue
        s = str(cand)
        if s in sys.path:
            if cand == _install_target() and sys.path[0] != s:
                sys.path.remove(s)
                sys.path.insert(0, s)
            continue
        if cand == _install_target():
            # This target contains plugin-private deps such as pycryptodome's
            # ``Crypto`` package. PyInstaller may already have a different
            # ``Crypto`` namespace in _internal; put our clean target first so
            # ``oss2`` sees a complete pycryptodome install.
            sys.path.insert(0, s)
        else:
            sys.path.append(s)
        appended.append(s)
    return appended


def _stale_module_prefixes(import_name: str) -> tuple[str, ...]:
    """Modules to evict before retrying an import after sys.path changes."""
    if import_name == "oss2":
        return (
            "oss2",
            "crcmod",
            # oss2 -> aliyunsdkcore -> pycryptodome imports Crypto.*.  Some
            # OpenAkita Desktop builds already import a bundled Crypto package
            # from _internal, which lacks Crypto.Util.Counter and shadows the
            # complete pycryptodome wheel we just installed. Clear it so the
            # next import resolves against the clean target inserted above.
            "Crypto",
            "aliyunsdkcore",
            "aliyunsdkkms",
        )
    return (import_name,)


def _clear_stale_modules(import_name: str) -> None:
    prefixes = _stale_module_prefixes(import_name)
    for stale in [
        m
        for m in sys.modules
        if any(m == p or m.startswith(p + ".") for p in prefixes)
    ]:
        sys.modules.pop(stale, None)


# ── interpreter resolution ──────────────────────────────────────────


def _resolve_python_executable() -> str:
    """Find a real Python interpreter to invoke ``-m pip`` against.

    In a PyInstaller-frozen build ``sys.executable`` is the wrapper
    ``openakita-server.exe`` which intercepts CLI args and refuses
    ``-m pip``. The real interpreter lives next to it as
    ``_internal/python.exe`` (Windows) / ``_internal/python3`` (Linux).
    For non-frozen / source / venv installs ``sys.executable`` *is* a
    real Python so we keep it.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable

    exe_dir = Path(sys.executable).parent
    internal = exe_dir if exe_dir.name == "_internal" else exe_dir / "_internal"
    if sys.platform == "win32":
        cands = [internal / "python.exe", internal / "python3.exe"]
    else:
        cands = [internal / "python3", internal / "python"]
    for c in cands:
        if c.exists():
            return str(c)
    return sys.executable  # caller will surface the failure


# ── pip install ─────────────────────────────────────────────────────


# Aliyun first (in-China users dominate); fall back to the official mirror
# only as a last resort because pypi.org regularly times out from China.
_PIP_MIRRORS: list[tuple[str, str]] = [
    ("https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
    ("https://pypi.tuna.tsinghua.edu.cn/simple/", "pypi.tuna.tsinghua.edu.cn"),
    ("https://pypi.mirrors.ustc.edu.cn/simple/", "pypi.mirrors.ustc.edu.cn"),
    ("https://pypi.org/simple/", ""),
]


def _pip_install(specs: list[str], target: Path) -> tuple[bool, str]:
    """Install ``specs`` into ``target`` using mirror fallback.

    Returns ``(ok, last_error_tail)`` so callers can compose a single
    user-facing message including the failing source's stderr tail.
    """
    target.mkdir(parents=True, exist_ok=True)
    py = _resolve_python_executable()

    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONPATH", None)
    py_path = Path(py)

    extra_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        # Avoid spawning a console window for the pip subprocess so the
        # Tauri app doesn't briefly flash a black box during install.
        extra_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    def _probe_runtime(probe_env: dict[str, str]) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                [py, "-c", "import encodings, pip; print('ok')"],
                env=probe_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                **extra_kwargs,
            )
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        return proc.returncode == 0, (proc.stderr or proc.stdout or "").strip()

    # Prefer the natural _internal/python.exe environment. Setting PYTHONHOME
    # unnecessarily can hide pip's vendored build backend and produce:
    # ``BackendUnavailable: Cannot import 'setuptools.build_meta'``.
    # Only add PYTHONHOME when the bare probe fails with the classic
    # "No module named encodings" bootstrap error.
    runtime_ok, probe_err = _probe_runtime(env)
    if (
        not runtime_ok
        and "encodings" in probe_err
        and getattr(sys, "frozen", False)
        and py_path.parent.name == "_internal"
    ):
        env["PYTHONHOME"] = str(py_path.parent)
        runtime_ok, probe_err = _probe_runtime(env)
    if not runtime_ok:
        return False, f"python runtime probe failed: {probe_err[-400:]}"

    last_err = ""
    for url, trusted in _PIP_MIRRORS:
        cmd = [
            py, "-m", "pip", "install",
            "--upgrade",
            "--prefer-binary",
            "--target", str(target),
            "-i", url,
        ]
        if trusted:
            cmd.extend(["--trusted-host", trusted])
        cmd.extend(specs)
        logger.info("avatar-studio dep install via %s: %s", url, specs)
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                **extra_kwargs,
            )
        except subprocess.TimeoutExpired as e:
            last_err = f"timeout via {url}: {e}"
            logger.warning(last_err)
            continue
        except Exception as e:  # noqa: BLE001 - any spawn error worth retry
            last_err = f"spawn error via {url}: {e}"
            logger.warning(last_err)
            continue
        if proc.returncode == 0:
            logger.info("avatar-studio dep install ok via %s", url)
            return True, ""
        # Keep only the tail of stderr so the UI message stays readable.
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        last_err = f"pip exit {proc.returncode} via {url}: {tail}"
        logger.warning(last_err)
    return False, last_err


# ── public API ──────────────────────────────────────────────────────


def ensure_importable(
    import_name: str,
    pip_spec: str,
    *,
    plugin_dir: Path | None = None,
    friendly_name: str | None = None,
) -> Any:
    """Import ``import_name`` or auto-install ``pip_spec`` and retry.

    On success returns the imported module object.  On failure raises
    ``DepInstallFailed`` with a human-readable message that already
    encodes the resolved interpreter and the install target — so the
    UI can surface it verbatim instead of the un-actionable
    ``ModuleNotFoundError`` PyInstaller-wrapped users see today.

    This function is **synchronous** by design: it's called from inside
    ``OssUploader._bucket`` which sits on the user's upload critical
    path. The first call may block ~10–30 s while pip downloads. Use
    ``preinstall_async`` from ``plugin.on_load`` to amortise that cost
    over app-startup time.
    """
    if import_name in _RESOLVED:
        return importlib.import_module(import_name)

    label = friendly_name or import_name

    # Do this before every import attempt. It is a no-op on healthy
    # environments and fixes OpenAkita Desktop builds where ``requests``
    # breaks while importing ``oss2`` because bundled ``simplejson`` is
    # missing JSONDecodeError.
    _patch_simplejson_jsondecodeerror()

    # Attempt 1: bare import (already on sys.path or already installed).
    try:
        mod = importlib.import_module(import_name)
        _RESOLVED.add(import_name)
        return mod
    except ImportError:
        pass

    # Attempt 2: append known-good site-packages dirs and retry.
    appended = _ensure_on_syspath(plugin_dir)
    if appended:
        importlib.invalidate_caches()
        _patch_simplejson_jsondecodeerror()
        # Drop failed-half-load cached entries and dependency namespaces that
        # may already point at PyInstaller's _internal directory.
        _clear_stale_modules(import_name)
        try:
            mod = importlib.import_module(import_name)
            _RESOLVED.add(import_name)
            logger.info("avatar-studio: %s imported after sys.path += %s", label, appended)
            return mod
        except ImportError:
            pass

    # Attempt 3: pip install. Hold the lock so concurrent calls don't
    # pip-install in parallel — let the second caller wait for the
    # first install to finish then re-probe.
    with _INSTALL_LOCK:
        if import_name in _RESOLVED:
            return importlib.import_module(import_name)

        target = _install_target()
        ok, err = _pip_install([pip_spec], target)
        if not ok:
            raise DepInstallFailed(
                f"无法自动安装 {label}（{pip_spec}）。"
                f"插件已尝试 4 个镜像源全部失败：{err}\n"
                "请检查网络后重试，或手动执行：\n"
                f"  {_resolve_python_executable()} -m pip install --target "
                f"{target} {pip_spec}",
            )

        # Make absolutely sure the freshly-installed dir is on sys.path.
        target_str = str(target)
        if target_str in sys.path:
            sys.path.remove(target_str)
        # Put the freshly installed clean target ahead of any legacy
        # ``site-packages`` path that may already be on sys.path with a
        # partially-written crcmod/oss2 from an earlier failed install.
        sys.path.insert(0, target_str)
        importlib.invalidate_caches()
        _patch_simplejson_jsondecodeerror()
        _clear_stale_modules(import_name)
        try:
            mod = importlib.import_module(import_name)
        except ImportError as e:
            raise DepInstallFailed(
                f"已下载 {label} 到 {target} 但仍无法 import：{e}",
                cause=e,
            ) from e
        _RESOLVED.add(import_name)
        logger.info("avatar-studio: %s installed and imported (%s)", label, target)
        return mod


def probe_dependency(
    dep_id: str,
    import_name: str,
    *,
    plugin_dir: Path | None = None,
) -> dict[str, Any]:
    """Return dependency status without auto-installing anything."""
    _patch_simplejson_jsondecodeerror()
    _ensure_on_syspath(plugin_dir)
    importlib.invalidate_caches()
    _clear_stale_modules(import_name)
    ok = False
    error = ""
    version = ""
    module_file = ""
    try:
        mod = importlib.import_module(import_name)
        ok = True
        module_file = str(getattr(mod, "__file__", "") or "")
        version = str(getattr(mod, "__version__", "") or "")
        if not version:
            try:
                from importlib import metadata

                version = metadata.version(dep_id)
            except Exception:
                version = ""
    except Exception as exc:  # noqa: BLE001 - diagnostic endpoint
        error = f"{type(exc).__name__}: {exc}"
    return {
        "id": dep_id,
        "import_name": import_name,
        "ok": ok,
        "version": version,
        "module_file": module_file,
        "error": error,
        "target": str(_install_target()),
        "candidate_dirs": [str(p) for p in _candidate_site_dirs(plugin_dir)],
        **_state_for(dep_id),
    }


def start_install(
    dep_id: str,
    import_name: str,
    pip_spec: str,
    *,
    plugin_dir: Path | None = None,
    friendly_name: str | None = None,
) -> dict[str, Any]:
    """Start a background install and return the current fire-and-poll state."""
    state = _DEP_STATE.setdefault(dep_id, {})
    if state.get("busy"):
        return probe_dependency(dep_id, import_name, plugin_dir=plugin_dir)

    def _worker() -> None:
        state.update(
            {
                "busy": True,
                "last_error": "",
                "last_started_at": time.time(),
                "last_finished_at": 0.0,
                "last_success": False,
            }
        )
        _log_dep(dep_id, f"install started: {pip_spec}")
        try:
            ensure_importable(
                import_name,
                pip_spec,
                plugin_dir=plugin_dir,
                friendly_name=friendly_name or import_name,
            )
            state["last_success"] = True
            _log_dep(dep_id, "install/import succeeded")
        except Exception as exc:  # noqa: BLE001 - background state
            msg = f"{type(exc).__name__}: {exc}"
            state["last_error"] = msg
            _log_dep(dep_id, msg)
            logger.warning("avatar-studio dep %s install failed: %s", dep_id, exc)
        finally:
            state["busy"] = False
            state["last_finished_at"] = time.time()

    t = threading.Thread(
        target=_worker,
        name=f"avatar-studio-dep-{dep_id}",
        daemon=True,
    )
    t.start()
    return probe_dependency(dep_id, import_name, plugin_dir=plugin_dir)


def preinstall_async(
    specs: list[tuple[str, str]],
    *,
    plugin_dir: Path | None = None,
) -> None:
    """Spawn a background thread that pre-installs every (import_name, pip_spec).

    Call this from ``plugin.on_load`` so the user's first upload doesn't
    pay the install latency. Safe to call when deps are already
    installed — ``ensure_importable`` short-circuits via ``_RESOLVED``.

    Errors here are intentionally **swallowed**. If preinstall fails,
    the synchronous call from the upload path will surface a proper
    error message; we don't want plugin load to fail just because pip
    couldn't reach a mirror at startup.
    """
    if not specs:
        return

    def _worker() -> None:
        for import_name, pip_spec in specs:
            try:
                ensure_importable(
                    import_name,
                    pip_spec,
                    plugin_dir=plugin_dir,
                    friendly_name=import_name,
                )
            except Exception as e:  # noqa: BLE001 - background, log only
                logger.info(
                    "avatar-studio preinstall %s skipped: %s",
                    import_name, e,
                )

    t = threading.Thread(
        target=_worker,
        name="avatar-studio-dep-bootstrap",
        daemon=True,
    )
    t.start()
