"""Plugin installation: URL, local path, bundle import, pip dependencies."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .bundles import BundleMapper
from .manifest import ManifestError, parse_manifest

logger = logging.getLogger(__name__)


class PluginInstallError(Exception):
    """Installation could not complete."""


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


def install_from_url(url: str, plugins_dir: Path) -> str:
    plugins_dir = plugins_dir.resolve()
    plugins_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="openakita-plugin-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "plugin.zip"
        _download_to_file(url, archive)

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

        try:
            manifest = parse_manifest(plugin_src)
        except ManifestError as e:
            raise PluginInstallError(str(e)) from e

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
        raise PluginInstallError(f"Plugin directory already exists: {dest} (id={plugin_id!r})")

    try:
        shutil.copytree(path, dest)
    except OSError as e:
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
        raise PluginInstallError(f"Could not write plugin.json: {e}") from e

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
