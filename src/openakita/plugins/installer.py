"""Plugin installation: URL, local path, bundle import, pip dependencies."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .bundles import BundleMapper
from .manifest import ManifestError, parse_manifest

logger = logging.getLogger(__name__)


class PluginInstallError(Exception):
    """Installation could not complete."""


# --- Windows-friendly file-system helpers -----------------------------------

_RMTREE_ATTEMPTS = 5
_RMTREE_BASE_DELAY = 0.2  # seconds; doubled each retry (max ~3.2 s total wait)


def _on_rm_error(func: Any, path: str, exc_info: Any) -> None:
    """``shutil.rmtree`` ``onerror`` hook: clear read-only bit then retry once."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        # Re-raise so the outer retry loop in _robust_rmtree can take over.
        raise


def _robust_rmtree(path: Path, *, attempts: int = _RMTREE_ATTEMPTS) -> bool:
    """Remove a directory tree with Windows-friendly retries.

    Windows often refuses to delete a path immediately after a process closed
    its handles (the kernel finalises the unlink lazily, especially across
    SQLite WAL/SHM files). Exponential-backoff retries, plus a ``chmod`` to
    clear the read-only bit, fix the vast majority of "[WinError 32] file in
    use" failures during plugin uninstall / hot-reload.

    Returns ``True`` on success; ``False`` if all retries fail. Never raises.
    """
    if not path.exists():
        return True
    last_err: OSError | None = None
    for i in range(attempts):
        try:
            shutil.rmtree(path, onerror=_on_rm_error)
        except OSError as e:
            last_err = e
        if not path.exists():
            return True
        time.sleep(_RMTREE_BASE_DELAY * (2**i))
    if last_err is not None:
        logger.warning(
            "Could not remove %s after %d attempts: %s", path, attempts, last_err
        )
    return False


def _robust_rename(src: Path, dst: Path, *, attempts: int = _RMTREE_ATTEMPTS) -> bool:
    """Rename a path with Windows-friendly retries. Never raises; returns success."""
    last_err: OSError | None = None
    for i in range(attempts):
        try:
            src.rename(dst)
            return True
        except OSError as e:
            last_err = e
            time.sleep(_RMTREE_BASE_DELAY * (2**i))
    if last_err is not None:
        logger.warning(
            "Could not rename %s -> %s after %d attempts: %s",
            src,
            dst,
            attempts,
            last_err,
        )
    return False


def _list_locked_files(plugin_dir: Path, *, max_items: int = 10) -> list[str]:
    """Probe each surviving file under ``plugin_dir`` to find likely locks.

    Used after ``_robust_rmtree`` fails so the user/diagnostics can see
    which files are actually held (DB vs log vs .pyc), narrowing the root
    cause without naming the holding process. Pure read-only probe — does
    NOT modify the filesystem.

    Heuristic: ``os.replace(f, f)`` (rename-to-self) is the correct probe
    for "can rmtree delete this?" on Windows. Renaming requires the
    ``DELETE`` access right and ``FILE_SHARE_DELETE`` from any other open
    handle — exactly the same combination ``os.unlink`` (and therefore
    ``shutil.rmtree``) needs. SQLite/aiosqlite, Python's ``RotatingFileHandler``,
    and most "open exclusively for write" handles deny ``FILE_SHARE_DELETE``,
    so they show up here even though they happily allow ``open(f, "ab")``.

    NOTE: an earlier version probed with ``open(f, "ab")``. That only checks
    ``FILE_SHARE_WRITE`` and silently missed the most common offender
    (a still-open SQLite connection from a leaked test/process), making the
    user-facing 207/409 error a generic "目录无法清理" with no filenames.
    """
    locked: list[str] = []
    try:
        for f in plugin_dir.rglob("*"):
            if not f.is_file():
                continue
            try:
                # Rename-to-self: identity op on success, raises OSError if
                # any other handle on the file denies DELETE share — i.e.
                # exactly the rmtree blocker we want to surface.
                os.replace(f, f)
            except OSError:
                try:
                    rel = f.relative_to(plugin_dir).as_posix()
                except ValueError:
                    rel = str(f)
                locked.append(rel)
                if len(locked) >= max_items:
                    break
    except OSError as e:
        logger.debug("Could not walk %s for lock probe: %s", plugin_dir, e)
    return locked


def _force_remove_db_files(plugin_dir: Path) -> bool:
    """Last-resort: delete any SQLite files so a reinstall is not blocked.

    Returns ``True`` if at least one file was removed.
    """
    cleaned = False
    try:
        for f in plugin_dir.rglob("*"):
            if not f.is_file():
                continue
            name = f.name.lower()
            if (
                name.endswith(".db")
                or name.endswith(".sqlite")
                or name.endswith(".sqlite3")
                or ".db-" in name  # *.db-shm, *.db-wal, *.db-journal
            ):
                try:
                    f.unlink()
                    cleaned = True
                except OSError as e:
                    logger.debug("Could not remove %s: %s", f, e)
    except OSError as e:
        logger.debug("Could not walk %s: %s", plugin_dir, e)
    return cleaned


class InstallProgress:
    """Thread-safe installation progress tracker.

    Usage from REST API:
        progress = InstallProgress()
        installer.install_from_url(url, dir, progress=progress)
        # Poll progress.snapshot() from SSE endpoint
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stage = "pending"
        self._message = ""
        self._percent = 0.0
        self._finished = False
        self._error = ""
        self._result: dict[str, Any] = {}
        self._updated_at = time.monotonic()

    def update(self, stage: str, message: str, percent: float = -1) -> None:
        with self._lock:
            self._stage = stage
            self._message = message
            if percent >= 0:
                self._percent = min(percent, 100.0)
            self._updated_at = time.monotonic()

    def finish(self, *, error: str = "", result: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._finished = True
            self._error = error
            self._stage = "error" if error else "done"
            self._percent = 100.0 if not error else self._percent
            self._result = result or {}
            self._updated_at = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap: dict[str, Any] = {
                "stage": self._stage,
                "message": self._message,
                "percent": self._percent,
                "finished": self._finished,
                "error": self._error,
            }
            if self._result:
                snap["result"] = dict(self._result)
            return snap


_active_installs: dict[str, InstallProgress] = {}
_active_installs_lock = threading.Lock()


def get_install_progress(install_id: str) -> InstallProgress | None:
    with _active_installs_lock:
        return _active_installs.get(install_id)


def _register_progress(install_id: str, progress: InstallProgress) -> None:
    with _active_installs_lock:
        _active_installs[install_id] = progress


def _unregister_progress(install_id: str) -> None:
    with _active_installs_lock:
        _active_installs.pop(install_id, None)


def _sanitize_dir_name(plugin_id: str) -> str:
    bad = '<>:"/\\|?*'
    s = "".join(c if c not in bad and ord(c) >= 32 else "_" for c in plugin_id)
    s = s.strip(". ") or "plugin"
    return s


def _find_plugin_json_root(root: Path) -> Path | None:
    candidates: list[Path] = []
    for p in root.rglob("plugin.json"):
        if p.is_file():
            candidates.append(p.parent)
    if not candidates:
        return None
    return min(candidates, key=lambda p: len(p.parts))


_MAX_EXTRACT_SIZE = 500 * 1024 * 1024  # 500 MB
_MAX_EXTRACT_FILES = 10_000


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    total_size = 0
    file_count = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        total_size += info.file_size
        file_count += 1
        if total_size > _MAX_EXTRACT_SIZE:
            raise PluginInstallError(
                f"Zip archive exceeds size limit ({_MAX_EXTRACT_SIZE // 1024 // 1024} MB)"
            )
        if file_count > _MAX_EXTRACT_FILES:
            raise PluginInstallError(f"Zip archive exceeds file count limit ({_MAX_EXTRACT_FILES})")
        name = info.filename
        if name.startswith("/") or ".." in Path(name).parts:
            raise PluginInstallError(f"Unsafe zip entry: {name!r}")
        target = (dest / name).resolve()
        try:
            target.relative_to(dest)
        except ValueError as e:
            raise PluginInstallError(f"Zip slip rejected: {name!r}") from e
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _download_to_file(url: str, dest: Path) -> None:
    req = Request(url, headers={"User-Agent": "OpenAkita-PluginInstaller/1.0"})
    try:
        with urlopen(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
    except HTTPError as e:
        raise PluginInstallError(f"HTTP {e.code} downloading plugin: {url}") from e
    except URLError as e:
        raise PluginInstallError(f"Network error downloading plugin: {e.reason}") from e
    except OSError as e:
        raise PluginInstallError(f"Download failed: {e}") from e


def install_pip_deps(plugin_dir: Path, manifest_requires: dict) -> bool:
    if not manifest_requires:
        return True
    raw = manifest_requires.get("pip")
    if raw is None:
        return True
    if isinstance(raw, str):
        specs = [raw] if raw.strip() else []
    elif isinstance(raw, list):
        specs = [str(x).strip() for x in raw if str(x).strip()]
    else:
        logger.warning("requires.pip must be a string or list of strings")
        return False
    if not specs:
        return True

    deps_dir = plugin_dir / "deps"
    deps_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--target",
        str(deps_dir),
        *specs,
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        logger.error("pip install timed out for %s", plugin_dir)
        return False
    if proc.returncode != 0:
        logger.error(
            "pip install failed for %s: %s",
            plugin_dir,
            (proc.stderr or proc.stdout or "").strip(),
        )
        return False
    return True


def _finalize_install(plugin_dir: Path, *, remove_on_failure: bool = True) -> str:
    try:
        manifest = parse_manifest(plugin_dir)
    except ManifestError as e:
        if remove_on_failure:
            shutil.rmtree(plugin_dir, ignore_errors=True)
        raise PluginInstallError(str(e)) from e
    if not install_pip_deps(plugin_dir, manifest.requires):
        if remove_on_failure:
            shutil.rmtree(plugin_dir, ignore_errors=True)
        raise PluginInstallError(f"Plugin {manifest.id!r} installed but pip dependencies failed")
    return manifest.id


_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar", ".tar.bz2", ".tar.xz")


def _is_git_url(source: str) -> bool:
    s = source.lower().strip()
    if s.endswith(".git"):
        return True
    if "github.com/" in s or "gitlab.com/" in s or "gitee.com/" in s:
        if any(s.endswith(ext) for ext in _ARCHIVE_SUFFIXES):
            return False
        if "/releases/" in s or "/archive/" in s or "/raw/" in s:
            return False
        return True
    return False


def _normalize_git_url(source: str) -> str:
    """Normalise GitHub/GitLab short URLs → cloneable .git URL.

    Handles: https://github.com/o/r, github.com/o/r, https://github.com/o/r/tree/...
    """
    s = source.strip().rstrip("/")
    if s.endswith(".git"):
        return s
    for host in ("github.com/", "gitlab.com/", "gitee.com/"):
        idx = s.find(host)
        if idx < 0:
            continue
        after_host = s[idx + len(host) :]
        segments = after_host.split("/")
        if len(segments) >= 2:
            owner_repo = s[: idx + len(host)] + "/".join(segments[:2])
            if not owner_repo.startswith(("http://", "https://", "git@")):
                owner_repo = "https://" + owner_repo
            return owner_repo + ".git"
    return s + ".git"


def install_from_git(
    source: str,
    plugins_dir: Path,
    *,
    branch: str = "",
    progress: InstallProgress | None = None,
) -> str:
    """Clone a Git repository and install the plugin from it."""
    plugins_dir = plugins_dir.resolve()
    plugins_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress.update("cloning", f"正在克隆仓库: {source[:80]}", 10)

    git_url = _normalize_git_url(source)

    with tempfile.TemporaryDirectory(prefix="openakita-git-") as tmp:
        tmp_path = Path(tmp)
        clone_dir = tmp_path / "repo"
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [git_url, str(clone_dir)]

        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            raise PluginInstallError(
                "git command not found — please install Git to use repository URLs"
            )
        except subprocess.TimeoutExpired:
            raise PluginInstallError("Git clone timed out (120s)")

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            raise PluginInstallError(f"Git clone failed: {err}")

        if progress:
            progress.update("validating", "正在验证插件清单", 50)

        plugin_src = _find_plugin_json_root(clone_dir)
        if plugin_src is None:
            raise PluginInstallError("No plugin.json found in cloned repository")

        try:
            manifest = parse_manifest(plugin_src)
        except ManifestError as e:
            raise PluginInstallError(str(e)) from e

        if progress:
            progress.update("installing", f"正在安装插件: {manifest.id}", 65)

        dest = plugins_dir / _sanitize_dir_name(manifest.id)
        backup = None
        if dest.exists():
            backup = dest.with_suffix(".bak")
            try:
                if backup.exists():
                    shutil.rmtree(backup)
                dest.rename(backup)
            except OSError as e:
                raise PluginInstallError(
                    f"Cannot upgrade: failed to backup existing plugin: {e}"
                ) from e

        git_internal = plugin_src / ".git"
        if git_internal.exists():
            shutil.rmtree(git_internal, ignore_errors=True)

        try:
            shutil.copytree(plugin_src, dest)
        except OSError as e:
            if backup is not None:
                try:
                    backup.rename(dest)
                except OSError:
                    pass
            raise PluginInstallError(f"Could not install plugin files: {e}") from e

    if progress:
        progress.update("dependencies", "正在安装依赖", 80)

    try:
        result = _finalize_install(dest)
    except PluginInstallError:
        if backup is not None and backup.exists():
            try:
                if dest.exists():
                    shutil.rmtree(dest)
                backup.rename(dest)
            except OSError:
                pass
        raise

    if backup is not None and backup.exists():
        shutil.rmtree(backup, ignore_errors=True)

    if progress:
        progress.update("done", f"插件 {result} 安装完成", 100)
    return result


def install_from_url(
    url: str,
    plugins_dir: Path,
    *,
    progress: InstallProgress | None = None,
) -> str:
    plugins_dir = plugins_dir.resolve()
    plugins_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress.update("downloading", f"正在下载: {url[:80]}", 10)

    with tempfile.TemporaryDirectory(prefix="openakita-plugin-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "plugin.zip"
        _download_to_file(url, archive)

        if progress:
            progress.update("extracting", "正在解压插件包", 40)

        extract_root = tmp_path / "extract"
        extract_root.mkdir()
        try:
            with zipfile.ZipFile(archive, "r") as zf:
                _safe_extract_zip(zf, extract_root)
        except zipfile.BadZipFile as e:
            raise PluginInstallError("Download is not a valid zip archive") from e

        plugin_src = _find_plugin_json_root(extract_root)
        if plugin_src is None:
            raise PluginInstallError("No plugin.json found in archive")

        if progress:
            progress.update("validating", "正在验证插件清单", 55)

        try:
            manifest = parse_manifest(plugin_src)
        except ManifestError as e:
            raise PluginInstallError(str(e)) from e

        if progress:
            progress.update("installing", f"正在安装插件: {manifest.id}", 65)

        dest = plugins_dir / _sanitize_dir_name(manifest.id)
        if dest.exists():
            backup = dest.with_suffix(".bak")
            try:
                if backup.exists():
                    shutil.rmtree(backup)
                dest.rename(backup)
            except OSError as e:
                raise PluginInstallError(
                    f"Cannot upgrade: failed to backup existing plugin: {e}"
                ) from e
        else:
            backup = None

        try:
            shutil.copytree(plugin_src, dest)
        except OSError as e:
            if backup is not None:
                try:
                    backup.rename(dest)
                except OSError:
                    pass
            raise PluginInstallError(f"Could not install plugin files: {e}") from e

    if progress:
        progress.update("dependencies", "正在安装依赖", 80)

    try:
        result = _finalize_install(dest)
    except PluginInstallError:
        if backup is not None and backup.exists():
            try:
                if dest.exists():
                    shutil.rmtree(dest)
                backup.rename(dest)
            except OSError:
                pass
        raise

    if backup is not None and backup.exists():
        shutil.rmtree(backup, ignore_errors=True)

    if progress:
        progress.update("done", f"插件 {result} 安装完成", 100)
    return result


def install_from_path(
    source: Path,
    plugins_dir: Path,
    *,
    dev_mode: bool = False,
) -> str:
    """Install a plugin from a local directory.

    When ``dev_mode`` is ``True`` we **symlink** instead of copying so source
    edits are immediately visible after a hot-reload — ideal for plugin
    developers. If the OS refuses the symlink (Windows without dev-mode /
    admin), we fall back transparently to a regular copy and emit a warning.
    """
    source = source.resolve()
    plugins_dir = plugins_dir.resolve()
    if not source.is_dir():
        raise PluginInstallError(f"Not a directory: {source}")

    try:
        manifest = parse_manifest(source)
    except ManifestError as e:
        raise PluginInstallError(str(e)) from e

    dest = plugins_dir / _sanitize_dir_name(manifest.id)
    plugins_dir.mkdir(parents=True, exist_ok=True)

    backup = None
    if dest.exists():
        try:
            same = dest.samefile(source)
        except OSError:
            same = False
        if same:
            return _finalize_install(dest, remove_on_failure=False)
        # If dest is already a symlink pointing at source, just refresh.
        if dest.is_symlink():
            try:
                if Path(os.readlink(dest)).resolve() == source:
                    return _finalize_install(dest, remove_on_failure=False)
            except OSError:
                pass
        backup = dest.with_suffix(".bak")
        if backup.exists():
            _robust_rmtree(backup)
        if not _robust_rename(dest, backup):
            raise PluginInstallError(
                f"Cannot upgrade: failed to backup existing plugin at {dest}"
            )

    linked = False
    if dev_mode:
        try:
            os.symlink(source, dest, target_is_directory=True)
            linked = True
            logger.info("Plugin '%s' linked from %s (dev mode)", manifest.id, source)
        except (OSError, NotImplementedError) as e:
            logger.warning(
                "Plugin '%s' dev-mode symlink failed (%s); falling back to copy",
                manifest.id,
                e,
            )

    if not linked:
        try:
            shutil.copytree(source, dest)
        except OSError as e:
            if backup is not None:
                _robust_rename(backup, dest)
            raise PluginInstallError(f"Could not copy plugin: {e}") from e

    try:
        result = _finalize_install(dest)
    except PluginInstallError:
        if backup is not None and backup.exists():
            try:
                if dest.exists():
                    if dest.is_symlink():
                        dest.unlink()
                    else:
                        _robust_rmtree(dest)
                _robust_rename(backup, dest)
            except OSError:
                pass
        raise

    if backup is not None and backup.exists():
        _robust_rmtree(backup)
    return result


def uninstall(
    plugin_id: str,
    plugins_dir: Path,
    *,
    purge_data: bool = False,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Remove a plugin from disk and optionally purge its persistent data.

    Returns a structured result dict (no exceptions for "expected" failures —
    the API layer turns these into HTTP responses with proper error codes):

    .. code-block:: python

        {
            "removed": bool,        # plugin code dir is gone
            "partial": bool,        # code dir survived but *.db* files cleaned
            "purged_data": bool,    # data_root/<plugin_id> also removed
            "warnings": [str, ...], # human-readable hints (UI surfaces these)
        }

    A symlinked plugin (dev mode) is unlinked, never recursively deleted.
    """
    plugins_dir = plugins_dir.resolve()
    out: dict[str, Any] = {
        "removed": False,
        "partial": False,
        "purged_data": False,
        "warnings": [],
    }

    if not plugins_dir.is_dir():
        out["warnings"].append(f"plugins dir not found: {plugins_dir}")
        return out

    target: Path | None = None
    for child in plugins_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            manifest = parse_manifest(child)
        except ManifestError:
            continue
        if manifest.id == plugin_id:
            target = child
            break

    if target is None:
        out["warnings"].append(f"plugin '{plugin_id}' not installed")
        return out

    # Symlink fast-path: just unlink, never delete the linked source tree.
    if target.is_symlink():
        try:
            target.unlink()
            out["removed"] = True
        except OSError as e:
            out["warnings"].append(f"failed to unlink {target}: {e}")
    else:
        if _robust_rmtree(target):
            out["removed"] = True
        else:
            # Probe which files survived — actionable info for the user
            # ("the DB is held" vs "a .pyc is held" points to very different
            # root causes; in practice it's almost always the SQLite WAL or a
            # log file that the plugin failed to close in on_unload).
            locked = _list_locked_files(target)
            if locked:
                preview = ", ".join(locked[:5])
                more = f" (+{len(locked) - 5} 更多)" if len(locked) > 5 else ""
                out["warnings"].append(
                    f"以下文件仍被占用: {preview}{more}"
                )
            # Graceful degradation: clear DB files so the next install isn't
            # blocked by a still-locked SQLite file inside the leftover dir.
            if _force_remove_db_files(target):
                out["partial"] = True
                out["warnings"].append(
                    f"目录 {target} 无法完全删除（可能仍被占用），"
                    "已清理其中的 *.db / *.db-shm / *.db-wal 文件"
                )
            else:
                out["warnings"].append(f"目录 {target} 完全无法清理")

    if purge_data and data_root is not None:
        data_root_resolved = data_root.resolve()
        data_dir = (data_root_resolved / plugin_id).resolve()
        try:
            data_dir.relative_to(data_root_resolved)
        except ValueError:
            out["warnings"].append(
                "data path traversal check failed; skipping purge_data"
            )
        else:
            if data_dir.exists():
                if _robust_rmtree(data_dir):
                    out["purged_data"] = True
                else:
                    out["warnings"].append(
                        f"plugin_data {data_dir} 无法完全删除"
                    )
            else:
                out["purged_data"] = True  # nothing to purge → success

    return out


def install_bundle(source: str, plugins_dir: Path) -> str:
    path = Path(source).expanduser().resolve()
    plugins_dir = plugins_dir.resolve()
    if not path.is_dir():
        raise PluginInstallError(f"Not a directory: {path}")

    mapper = BundleMapper()
    bundle = mapper.detect(path)
    if bundle is None:
        raise PluginInstallError(f"No supported bundle format under {path}")

    manifest_dict = mapper.map_to_manifest(bundle)
    plugin_id = str(manifest_dict.get("id", ""))
    if not plugin_id:
        raise PluginInstallError("Bundle mapping produced no plugin ID")
    dest = plugins_dir / _sanitize_dir_name(plugin_id)
    plugins_dir.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        backup = dest.with_suffix(".bak")
        try:
            if backup.exists():
                shutil.rmtree(backup)
            dest.rename(backup)
        except OSError as e:
            raise PluginInstallError(
                f"Cannot upgrade bundle: failed to backup existing plugin: {e}"
            ) from e
    else:
        backup = None

    try:
        shutil.copytree(path, dest)
    except OSError as e:
        if backup is not None:
            try:
                backup.rename(dest)
            except OSError:
                pass
        raise PluginInstallError(f"Could not copy bundle: {e}") from e

    manifest_path = dest / "plugin.json"
    try:
        manifest_path.write_text(
            json.dumps(manifest_dict, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        try:
            shutil.rmtree(dest)
        except OSError:
            logger.warning("Could not remove partial install at %s", dest)
        if backup is not None:
            try:
                backup.rename(dest)
            except OSError:
                pass
        raise PluginInstallError(f"Could not write plugin.json: {e}") from e

    if backup is not None:
        try:
            shutil.rmtree(backup)
        except OSError:
            pass

    return _finalize_install(dest)
