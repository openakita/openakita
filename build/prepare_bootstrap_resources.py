#!/usr/bin/env python3
"""Prepare lightweight Tauri bootstrap resources for the dual-venv runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
BOOTSTRAP_DIR = ROOT / "apps" / "setup-center" / "src-tauri" / "resources" / "bootstrap"
BIN_DIR = BOOTSTRAP_DIR / "bin"
WHEELS_DIR = BOOTSTRAP_DIR / "wheels"
WHEELHOUSE_DIR = BOOTSTRAP_DIR / "wheelhouse"
DIST_DIR = ROOT / "dist"
BUILD_BOOTSTRAP_WHEELS = ROOT / "build" / "bootstrap-wheels"
BUILD_BOOTSTRAP_UV = ROOT / "build" / "bootstrap-uv"
BUILD_BOOTSTRAP_PYTHON = ROOT / "build" / "bootstrap-python"
DEFAULT_OUTPUT_ROOT = ROOT / "build" / "bootstrap-output"
WEB_ASSETS_DIR = ROOT / "apps" / "setup-center" / "dist-web"
DOCS_ASSETS_DIR = ROOT / "docs-site" / ".vitepress" / "dist"

UV_RELEASES = {
    ("Windows", "AMD64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
    ("Windows", "ARM64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-pc-windows-msvc.zip",
    ("Darwin", "arm64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz",
    ("Darwin", "x86_64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-apple-darwin.tar.gz",
    ("Linux", "x86_64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz",
    ("Linux", "aarch64"): "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-unknown-linux-gnu.tar.gz",
}

# python-build-standalone release tag and CPython version.
# 必须显式 pin —— 不允许从 "latest" 拉，避免 CI/release 出现"今天打的包跟
# 上周打的包用了不同 ABI"。升级时调这两个常量并跑全平台 release-dryrun 验证。
# 当前 pin 与 PyInstaller bundled 后端 (cp311) 严格对齐。
PBS_RELEASE_TAG = "20250409"
PBS_PYTHON_VERSION = "3.11.9"

# Map plan-side "target-platform" → python-build-standalone triple.
# 注意：PBS 的 windows arm64 长期不支持，因此 win-arm64 暂未列入。
# install_only_stripped 变种已剥 tests/docs/__pycache__，安装包 ≈ 25MB。
PBS_TARGETS: dict[str, str] = {
    "win-x64": "x86_64-pc-windows-msvc",
    "mac-x64": "x86_64-apple-darwin",
    "mac-arm64": "aarch64-apple-darwin",
    "linux-x64": "x86_64-unknown-linux-gnu",
    "linux-arm64": "aarch64-unknown-linux-gnu",
}


def bootstrap_paths(output_dir: Path) -> tuple[Path, Path, Path, Path]:
    bootstrap_dir = output_dir
    return (
        bootstrap_dir,
        bootstrap_dir / "bin",
        bootstrap_dir / "wheels",
        bootstrap_dir / "wheelhouse",
    )


def _has_real_asset_tree(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(item.is_file() and item.name != ".keep" for item in path.rglob("*"))


def validate_force_include_assets(*, require_real_assets: bool, allow_placeholder_assets: bool) -> None:
    missing: list[str] = []
    for label, path, command in (
        (
            "web frontend",
            WEB_ASSETS_DIR,
            "cd apps/setup-center && npm ci && npm run build:web",
        ),
        (
            "user docs",
            DOCS_ASSETS_DIR,
            "cd docs-site && npm ci && npm run build",
        ),
    ):
        has_real_assets = _has_real_asset_tree(path)
        if require_real_assets and not has_real_assets:
            missing.append(f"{label}: {path} (build with `{command}`)")
        elif not require_real_assets and not path.is_dir():
            missing.append(f"{label}: {path} (build with `{command}` or pass --allow-placeholder-assets)")

    if missing and not allow_placeholder_assets:
        details = "\n  - ".join(missing)
        raise RuntimeError(
            "Required force-include assets are missing or empty:\n"
            f"  - {details}\n"
            "Release/dry-run packaging must pass --require-real-assets after building web/docs. "
            "For CI contract-only validation, pass --allow-placeholder-assets explicitly."
        )

    if missing and allow_placeholder_assets:
        for path in (WEB_ASSETS_DIR, DOCS_ASSETS_DIR):
            path.mkdir(parents=True, exist_ok=True)
            keep = path / ".keep"
            if not keep.exists():
                keep.write_text("", encoding="utf-8")
        print("Using placeholder force-include assets for bootstrap contract validation only.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pyproject() -> dict:
    import tomllib

    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def build_wheel() -> Path:
    out_dir = BUILD_BOOTSTRAP_WHEELS
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=ROOT,
        check=True,
    )
    wheels = sorted(out_dir.glob("openakita-*.whl"), key=lambda item: item.stat().st_mtime)
    if not wheels:
        raise RuntimeError("python -m build --wheel completed but no openakita wheel was found")
    return wheels[-1]


def copy_wheel(wheel: Path, wheels_dir: Path) -> Path:
    wheels_dir.mkdir(parents=True, exist_ok=True)
    for old in wheels_dir.glob("openakita-*.whl"):
        old.unlink()
    target = wheels_dir / wheel.name
    shutil.copy2(wheel, target)
    return target


def download_uv(url: str, bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    uv_name = "uv.exe" if platform.system() == "Windows" else "uv"
    uv_target = bin_dir / uv_name
    if uv_target.exists():
        return uv_target

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / url.rsplit("/", 1)[-1]
        urllib.request.urlretrieve(url, archive)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                candidate = next(name for name in zf.namelist() if name.endswith(uv_name))
                with zf.open(candidate) as src, uv_target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            import tarfile

            with tarfile.open(archive) as tf:
                candidate = next(member for member in tf.getmembers() if member.name.endswith("/uv"))
                src = tf.extractfile(candidate)
                if src is None:
                    raise RuntimeError("uv archive did not contain an executable")
                with src, uv_target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

    if platform.system() != "Windows":
        uv_target.chmod(0o755)
    return uv_target


def install_uv(bin_dir: Path) -> Path:
    shutil.rmtree(BUILD_BOOTSTRAP_UV, ignore_errors=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(BUILD_BOOTSTRAP_UV),
            "--prefer-binary",
            "uv",
        ],
        cwd=ROOT,
        check=True,
    )
    uv_bin_name = "uv.exe" if os.name == "nt" else "uv"
    candidates = [
        BUILD_BOOTSTRAP_UV / "bin" / uv_bin_name,
        BUILD_BOOTSTRAP_UV / "Scripts" / uv_bin_name,
    ]
    uv_src = next((path for path in candidates if path.exists()), None)
    if uv_src is None:
        raise RuntimeError(f"uv binary not found in bootstrap-uv target: {candidates}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    uv_dest = bin_dir / uv_bin_name
    shutil.copy2(uv_src, uv_dest)
    uv_dest.chmod(uv_dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return uv_dest


def find_uv_url() -> str:
    system = platform.system()
    machine = platform.machine()
    key = (system, machine)
    if key not in UV_RELEASES:
        raise RuntimeError(f"Unsupported platform for uv bootstrap: {system} {machine}")
    return UV_RELEASES[key]


# ── python-build-standalone (PBS) seed handling ──
#
# 目标：把一份纯净 CPython 解释器打进 `resources/bootstrap/python/`，让 Tauri
# Rust 端 `managed_python_seed_path()` 命中、用 seed 的绝对路径喂给
# `uv venv --python <abs_path>`。这样首次启动无需联网拉 managed Python，也
# 完全规避了用户系统里 anaconda / pyenv / homebrew / mise / asdf 的污染。
#
# 跟 wheel/uv 类似，跑时 manifest 是单一可信源：Rust 端只读 manifest，写入
# 由本文件统一负责。`packaged: true` 才算真正打了 seed；下载/校验/smoke 任何
# 一步失败都拒绝写 true，避免发出"声称有 seed 但实际没有"的安装包。


def detect_local_target_platform() -> str:
    """Return the plan-side target-platform string matching the current host.

    本地开发跑 `prepare_bootstrap_resources.py` 时不必显式传
    `--target-platform`，自动推断更顺手。CI/release 严格要求显式传入，避免
    在 macOS Intel runner 上误把 mac-arm64 archive 装进 x64 安装包。
    """
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        if machine in {"amd64", "x86_64"}:
            return "win-x64"
        raise RuntimeError(f"unsupported Windows machine for PBS: {machine}")
    if system == "Darwin":
        if machine in {"arm64", "aarch64"}:
            return "mac-arm64"
        if machine in {"x86_64", "amd64"}:
            return "mac-x64"
        raise RuntimeError(f"unsupported macOS machine for PBS: {machine}")
    if system == "Linux":
        if machine in {"aarch64", "arm64"}:
            return "linux-arm64"
        if machine in {"x86_64", "amd64"}:
            return "linux-x64"
        raise RuntimeError(f"unsupported Linux machine for PBS: {machine}")
    raise RuntimeError(f"unsupported host platform for PBS: {system}")


def pbs_archive_name(target_platform: str) -> str:
    triple = PBS_TARGETS[target_platform]
    return (
        f"cpython-{PBS_PYTHON_VERSION}+{PBS_RELEASE_TAG}-{triple}-install_only_stripped.tar.gz"
    )


def pbs_archive_url(target_platform: str) -> str:
    name = pbs_archive_name(target_platform)
    return (
        f"https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{PBS_RELEASE_TAG}/{name}"
    )


def _download_pbs_archive(target_platform: str) -> tuple[Path, str]:
    """Download archive + companion `.sha256`, verify, return (path, digest).

    缓存到 `build/bootstrap-python/`：CI 二次跑同一 target_platform 不再重复
    下载 30MB。`.sha256` 文件本身也缓存，避免上游瞬时故障导致 release fail。
    """
    BUILD_BOOTSTRAP_PYTHON.mkdir(parents=True, exist_ok=True)
    archive_name = pbs_archive_name(target_platform)
    archive_path = BUILD_BOOTSTRAP_PYTHON / archive_name
    sha_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    url = pbs_archive_url(target_platform)
    sha_url = url + ".sha256"

    if not archive_path.exists():
        print(f"Downloading {url} → {archive_path}")
        with urllib.request.urlopen(url) as resp, archive_path.open("wb") as fh:
            shutil.copyfileobj(resp, fh)

    if not sha_path.exists():
        print(f"Downloading {sha_url} → {sha_path}")
        with urllib.request.urlopen(sha_url) as resp, sha_path.open("wb") as fh:
            shutil.copyfileobj(resp, fh)

    # PBS sha256 files have the form: "<digest>  <archive_name>\n"
    expected = sha_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256(archive_path)
    if actual.lower() != expected:
        # 删档失败的缓存让下一次 CI 跑能重新下载，避免持续踩坑。
        archive_path.unlink(missing_ok=True)
        sha_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"python-build-standalone sha256 mismatch for {archive_name}: "
            f"expected {expected}, got {actual.lower()}"
        )
    return archive_path, actual.lower()


def _safe_extract_tar(archive_path: Path, dest_dir: Path) -> None:
    """Safe tarball extract: refuse absolute paths and `..` traversal.

    PBS 是可信源，但每次解压前仍走一遍 hardening；防止上游 future
    accident 或 MITM 改包导致 path escape 写到 bootstrap_dir 外面。
    Python 3.12+ 有 tarfile.data_filter，但 CI 还在跑 3.11，所以自实现一遍。
    """
    import tarfile

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_dir.resolve()
    with tarfile.open(archive_path) as tf:
        members = []
        for member in tf.getmembers():
            target = (dest_dir / member.name).resolve()
            try:
                target.relative_to(dest_resolved)
            except ValueError as exc:
                raise RuntimeError(
                    f"refusing unsafe tar member {member.name!r} in {archive_path}"
                ) from exc
            if member.islnk() or member.issym():
                link_target = (dest_dir / member.name).parent / member.linkname
                try:
                    link_target.resolve().relative_to(dest_resolved)
                except ValueError as exc:
                    raise RuntimeError(
                        f"refusing unsafe link target {member.linkname!r} in {archive_path}"
                    ) from exc
            members.append(member)
        # Python 3.12+: pass filter='data' for extra defense in depth.
        if sys.version_info >= (3, 12):
            tf.extractall(dest_dir, members=members, filter="data")
        else:
            tf.extractall(dest_dir, members=members)


def _python_seed_root(bootstrap_dir: Path) -> Path:
    return bootstrap_dir / "python"


def _seed_binary_path(python_root: Path, target_platform: str) -> Path:
    if target_platform.startswith("win"):
        return python_root / "python.exe"
    # PBS unix layout: python/bin/python3.11
    major_minor = ".".join(PBS_PYTHON_VERSION.split(".")[:2])
    return python_root / "bin" / f"python{major_minor}"


def _slim_python_seed(python_root: Path, target_platform: str) -> None:
    """Remove docs/tests/IDLE/tkinter/turtledemo + __pycache__ to shave size."""
    if target_platform.startswith("win"):
        lib_roots = [python_root / "Lib"]
    else:
        lib_base = python_root / "lib"
        lib_roots = list(lib_base.glob("python3.*")) if lib_base.is_dir() else []
    for lib_root in lib_roots:
        for sub in ("test", "idlelib", "tkinter", "turtledemo"):
            target = lib_root / sub
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
    for cache_dir in python_root.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    if not target_platform.startswith("win"):
        for sub in ("share/man", "share/doc"):
            target = python_root / sub
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)


def _chmod_python_seed(python_root: Path, target_platform: str) -> None:
    """Defensive 0o755 on POSIX seed binaries.

    NTFS 不保留 Unix mode，如果 CI artefact 在 Windows runner 上中转或被某些
    解压工具重置 mode，用户端 `python3.11` 会变成 0o644 → spawn 时 EACCES。
    这里在打包阶段就把 mode 钉死，与运行时 Rust 兜底 chmod 形成双保险。
    """
    if target_platform.startswith("win"):
        return
    bin_dir = python_root / "bin"
    if bin_dir.is_dir():
        for entry in bin_dir.iterdir():
            if entry.is_file() or entry.is_symlink():
                try:
                    entry.chmod(0o755)
                except OSError:
                    pass
    for pattern in ("*.so", "*.so.*", "*.dylib"):
        for path in python_root.rglob(pattern):
            try:
                path.chmod(0o755)
            except OSError:
                pass


def _smoke_test_seed(seed_python: Path) -> None:
    """Verify seed Python can import essential stdlib modules.

    跳过 cross-arch / cross-OS 场景（Windows runner 验证 mac arm 包不可能跑）；
    上层 caller 会判断 host vs target 决定要不要调本函数。
    """
    code = (
        "import ssl, ctypes, zlib, sqlite3, hashlib, json, platform; "
        "print(platform.python_version())"
    )
    result = subprocess.run(
        [str(seed_python), "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"python-build-standalone seed smoke test failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    version = (result.stdout or "").strip()
    if not version.startswith(PBS_PYTHON_VERSION):
        raise RuntimeError(
            f"seed Python reported version {version!r}, expected to start with {PBS_PYTHON_VERSION!r}"
        )


def _host_can_exec_target(target_platform: str) -> bool:
    """Return True iff `target_platform` binary can actually run on this host.

    macOS host with Rosetta 在 mac-x64 上能跑，arm64 only host 不能跑 x64。
    保守起见，只在 host 与 target arch 严格一致时跑 smoke。
    """
    system = platform.system()
    machine = platform.machine().lower()
    if target_platform == "win-x64":
        return system == "Windows" and machine in {"amd64", "x86_64"}
    if target_platform == "mac-arm64":
        return system == "Darwin" and machine in {"arm64", "aarch64"}
    if target_platform == "mac-x64":
        return system == "Darwin" and machine in {"x86_64", "amd64"}
    if target_platform == "linux-arm64":
        return system == "Linux" and machine in {"aarch64", "arm64"}
    if target_platform == "linux-x64":
        return system == "Linux" and machine in {"x86_64", "amd64"}
    return False


def prepare_python_seed(
    bootstrap_dir: Path,
    target_platform: str,
    *,
    require_real_assets: bool,
) -> dict:
    """Download + verify + extract + slim + chmod PBS Python.

    Returns the dict to embed under manifest["python_seed"].  If
    require_real_assets is False and any step fails, returns a placeholder
    seed-not-packaged dict instead of raising — to keep local staging cheap.
    """
    if target_platform not in PBS_TARGETS:
        raise RuntimeError(f"unsupported target-platform for PBS: {target_platform!r}")

    try:
        archive_path, archive_sha = _download_pbs_archive(target_platform)
    except Exception as exc:
        if require_real_assets:
            raise
        print(f"[WARN] python-build-standalone download failed: {exc}; skipping seed packaging")
        return {
            "packaged": False,
            "path": "",
            "note": "Download failed during prepare; runtime falls back to managed/bundled Python.",
        }

    python_root = _python_seed_root(bootstrap_dir)
    if python_root.exists():
        shutil.rmtree(python_root, ignore_errors=True)

    _safe_extract_tar(archive_path, bootstrap_dir)

    if not python_root.is_dir():
        raise RuntimeError(
            f"PBS archive {archive_path.name} did not produce expected layout under {python_root}"
        )

    _slim_python_seed(python_root, target_platform)
    _chmod_python_seed(python_root, target_platform)

    seed_python = _seed_binary_path(python_root, target_platform)
    if not seed_python.exists():
        raise RuntimeError(f"seed Python binary missing after extraction: {seed_python}")

    if _host_can_exec_target(target_platform):
        _smoke_test_seed(seed_python)
    else:
        print(
            f"[INFO] skipping seed smoke test: host {platform.system()}/{platform.machine()} "
            f"cannot execute target {target_platform!r}"
        )

    rel_path = seed_python.relative_to(bootstrap_dir).as_posix()
    return {
        "packaged": True,
        "path": rel_path,
        "sha256": archive_sha,
        "version": PBS_PYTHON_VERSION,
        "release_tag": PBS_RELEASE_TAG,
        "source_url": pbs_archive_url(target_platform),
        "target_triple": PBS_TARGETS[target_platform],
        "target_platform": target_platform,
        "archive_name": pbs_archive_name(target_platform),
    }


def write_manifest(
    app_version: str,
    wheel: Path,
    uv: Path,
    bootstrap_dir: Path,
    python_seed: dict | None = None,
) -> None:
    manifest_path = bootstrap_dir / "manifest.json"
    wheel_name = wheel.relative_to(bootstrap_dir).as_posix()
    uv_name = uv.relative_to(bootstrap_dir).as_posix()
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "schema_version": 1,
            "app_name": "openakita",
            "app_version": app_version,
            "default_pip_index": {
                "id": "aliyun",
                "url": "https://mirrors.aliyun.com/pypi/simple/",
                "trusted_host": "mirrors.aliyun.com",
            },
        }
    manifest["app_version"] = app_version
    # python_version 必须与 PBS seed 的 major.minor 一致；当 packaged=true 时
    # 直接以 seed 为准，避免本机 host Python 版本飘移污染 manifest。
    if python_seed and python_seed.get("packaged"):
        seed_ver = str(python_seed.get("version") or PBS_PYTHON_VERSION)
        manifest["python_version"] = ".".join(seed_ver.split(".")[:2])
    else:
        manifest["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}"
    manifest["python_abi"] = sysconfig.get_config_var("SOABI") or f"cp{sys.version_info.major}{sys.version_info.minor}"
    manifest["wheel_tag"] = "py3-none-any"
    manifest["wheel"] = {
        "name": wheel_name,
        "sha256": sha256(wheel),
    }
    manifest.setdefault("uv", {})
    manifest["uv"]["path"] = uv_name
    manifest["uv"]["windows_path"] = "bin/uv.exe"
    manifest["uv"]["sha256"] = sha256(uv)
    if python_seed is not None:
        # 显式覆盖（而非 setdefault）：一旦本次 build 决定 seed=packaged=true，
        # 必须把之前可能残留的 false placeholder 真正替换掉。
        manifest["python_seed"] = python_seed
    else:
        manifest.setdefault(
            "python_seed",
            {
                "packaged": False,
                "path": "",
                "note": "Reserved for managed Python seed packaging; release workflows currently create app/agent venvs from bootstrap Python version instead.",
            },
        )
    manifest.setdefault(
        "node_seed",
        {
            "packaged": False,
            "path": "",
            "note": "Reserved for managed Node.js seed packaging; release workflows currently do not bundle Node.js.",
        },
    )
    manifest.setdefault("third_party", {})
    manifest["third_party"].setdefault(
        "uv",
        {"license": "Apache-2.0 OR MIT", "source": "https://github.com/astral-sh/uv"},
    )
    manifest["third_party"].setdefault(
        "python",
        {"license": "Python-2.0", "source": "https://www.python.org/"},
    )
    manifest["third_party"].setdefault(
        "node",
        {"license": "MIT", "source": "https://nodejs.org/"},
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_manifest(bootstrap_dir: Path = BOOTSTRAP_DIR) -> None:
    manifest_path = bootstrap_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wheel = bootstrap_dir / manifest["wheel"]["name"]
    if not wheel.is_file():
        raise RuntimeError(f"bootstrap wheel missing: {wheel}")
    if sha256(wheel) != manifest["wheel"]["sha256"]:
        raise RuntimeError("bootstrap wheel hash mismatch")
    uv_path = manifest["uv"].get("windows_path" if os.name == "nt" else "path") or manifest["uv"].get("path")
    uv = bootstrap_dir / uv_path
    if not uv.is_file():
        raise RuntimeError(f"uv binary missing: {uv}")
    if sha256(uv) != manifest["uv"]["sha256"]:
        raise RuntimeError("uv hash mismatch")

    seed = manifest.get("python_seed") or {}
    if seed.get("packaged"):
        seed_path = bootstrap_dir / seed["path"]
        if not seed_path.is_file():
            raise RuntimeError(f"python seed binary missing: {seed_path}")
        # POSIX 平台校验 exec bit；Windows 跳过（FAT/NTFS 不记录 mode）。
        if os.name != "nt" and not os.access(seed_path, os.X_OK):
            raise RuntimeError(f"python seed binary missing executable bit: {seed_path}")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def clean_output_dir(output_dir: Path) -> None:
    if output_dir.resolve() == BOOTSTRAP_DIR.resolve():
        return
    if not _is_within(output_dir, ROOT / "build"):
        raise RuntimeError(f"Refusing to clean non-build output directory: {output_dir}")
    shutil.rmtree(output_dir, ignore_errors=True)


def print_output_summary(output_dir: Path, generated: list[Path], *, commit_resources: bool) -> None:
    mode = "tracked Tauri resources" if commit_resources else "gitignored staging output"
    print(f"Prepared bootstrap resources in {output_dir} ({mode})")
    for item in generated:
        print(f"Generated: {item}")
    if commit_resources:
        print(
            "These files are release resources under apps/setup-center/src-tauri/resources/bootstrap; "
            "review them intentionally before committing."
        )
    else:
        print("Staging output lives under build/ and is ignored; do not commit generated binaries from it.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-wheel-build", action="store_true")
    parser.add_argument("--uv-url", default=os.environ.get("OPENAKITA_UV_URL"))
    parser.add_argument("--download-uv", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--require-real-assets", action="store_true")
    parser.add_argument("--allow-placeholder-assets", action="store_true")
    parser.add_argument(
        "--commit-resources",
        action="store_true",
        help="write directly into Tauri bootstrap resources; intended for CI/release packaging",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="staging output directory for local validation (default: build/bootstrap-output)",
    )
    parser.add_argument("--clean-output", action="store_true", help="clean staged output before writing")
    parser.add_argument(
        "--target-platform",
        choices=sorted(PBS_TARGETS.keys()),
        default=None,
        help=(
            "Package python-build-standalone for the given target platform "
            "(win-x64 / mac-x64 / mac-arm64 / linux-x64 / linux-arm64). "
            "CI/release MUST pass this; local staging may omit to skip seed packaging."
        ),
    )
    parser.add_argument(
        "--skip-python-seed",
        action="store_true",
        help="Force skip Python seed packaging even when --target-platform is given.",
    )
    parser.add_argument(
        "--auto-detect-target-platform",
        action="store_true",
        help="Auto-detect target platform from host (for local convenience; CI must NOT use this).",
    )
    args = parser.parse_args()
    if args.require_real_assets and args.allow_placeholder_assets:
        parser.error("--require-real-assets and --allow-placeholder-assets are mutually exclusive")
    if args.target_platform and args.auto_detect_target_platform:
        parser.error("--target-platform and --auto-detect-target-platform are mutually exclusive")

    output_dir = BOOTSTRAP_DIR if args.commit_resources else (args.output_dir or DEFAULT_OUTPUT_ROOT)
    if args.verify_only:
        verify_manifest(output_dir)
        print("bootstrap manifest verified")
        return 0

    project = load_pyproject()
    app_version = project["project"]["version"]

    if args.clean_output:
        clean_output_dir(output_dir)
    bootstrap_dir, bin_dir, wheels_dir, wheelhouse_dir = bootstrap_paths(output_dir)
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    wheelhouse_dir.mkdir(parents=True, exist_ok=True)
    validate_force_include_assets(
        require_real_assets=args.require_real_assets,
        allow_placeholder_assets=args.allow_placeholder_assets,
    )

    wheel = sorted(DIST_DIR.glob("openakita-*.whl"), key=lambda item: item.stat().st_mtime)[-1] if args.skip_wheel_build else build_wheel()
    packaged_wheel = copy_wheel(wheel, wheels_dir)
    uv = download_uv(args.uv_url or find_uv_url(), bin_dir) if args.download_uv else install_uv(bin_dir)

    # ── python-build-standalone seed ──
    # 选择 target platform：
    #   * 显式 --target-platform → 用之；
    #   * --auto-detect-target-platform → 从 host 推断（本地方便用，禁止 CI）；
    #   * 都没给 → 跳过 seed，manifest 仍写 packaged=false。
    # `--skip-python-seed` 总是 win。
    seed_info: dict | None = None
    if not args.skip_python_seed:
        chosen_target: str | None = args.target_platform
        if chosen_target is None and args.auto_detect_target_platform:
            chosen_target = detect_local_target_platform()
            print(f"[INFO] auto-detected target platform: {chosen_target}")
        if chosen_target is not None:
            seed_info = prepare_python_seed(
                bootstrap_dir,
                chosen_target,
                require_real_assets=args.require_real_assets,
            )

    write_manifest(app_version, packaged_wheel, uv, bootstrap_dir, python_seed=seed_info)
    verify_manifest(bootstrap_dir)

    generated = [bootstrap_dir / "manifest.json", packaged_wheel, uv]
    if seed_info and seed_info.get("packaged"):
        generated.append(bootstrap_dir / seed_info["path"])
    print_output_summary(
        bootstrap_dir,
        generated,
        commit_resources=args.commit_resources,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
