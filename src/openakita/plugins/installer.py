"""Plugin installation: URL, local path, bundle import, pip dependencies."""

from __future__ import annotations

import json
import logging
import shutil
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
            raise PluginInstallError(
                f"Zip archive exceeds file count limit ({_MAX_EXTRACT_FILES})"
            )
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
        after_host = s[idx + len(host):]
        segments = after_host.split("/")
        if len(segments) >= 2:
            owner_repo = s[: idx + len(host)] + "/".join(segments[:2])
            if not owner_repo.startswith(("http://", "https://", "git@")):
                owner_repo = "https://" + owner_repo
            return owner_repo + ".git"
    return s + ".git"


def install_from_git(
    source: str, plugins_dir: Path, *, branch: str = "",
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
                cmd, check=False, capture_output=True, text=True, timeout=120,
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
    url: str, plugins_dir: Path, *, progress: InstallProgress | None = None,
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


def install_from_path(source: Path, plugins_dir: Path) -> str:
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
        backup = dest.with_suffix(".bak")
        try:
            if backup.exists():
                shutil.rmtree(backup)
            dest.rename(backup)
        except OSError as e:
            raise PluginInstallError(
                f"Cannot upgrade: failed to backup existing plugin: {e}"
            ) from e

    try:
        shutil.copytree(source, dest)
    except OSError as e:
        if backup is not None:
            try:
                backup.rename(dest)
            except OSError:
                pass
        raise PluginInstallError(f"Could not copy plugin: {e}") from e

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
    return result


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


def uninstall(plugin_id: str, plugins_dir: Path) -> bool:
    plugins_dir = plugins_dir.resolve()
    if not plugins_dir.is_dir():
        return False

    for child in plugins_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            manifest = parse_manifest(child)
        except ManifestError:
            continue
        if manifest.id == plugin_id:
            try:
                shutil.rmtree(child)
            except OSError as e:
                logger.error("Could not remove %s: %s", child, e)
                return False
            return True
    return False
