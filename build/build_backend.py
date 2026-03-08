#!/usr/bin/env python3
"""
OpenAkita Python Backend Build Script

Usage:
  python build/build_backend.py --mode core    # Core package (~100-150MB)
  python build/build_backend.py --mode full    # Full package (~600-800MB)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = PROJECT_ROOT / "build" / "openakita.spec"
DIST_DIR = PROJECT_ROOT / "dist"
OUTPUT_DIR = DIST_DIR / "openakita-server"


def run_cmd(cmd: list[str], env: dict | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Run command and print output"""
    print(f"  $ {' '.join(cmd)}")
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(cmd, env=merged_env, **kwargs)
    if result.returncode != 0:
        print(f"  [ERROR] Command failed (exit {result.returncode})")
        sys.exit(1)
    return result


def ensure_bundled_pth_file(output_dir: Path) -> None:
    """Create python3XX._pth in _internal/ so standalone python.exe finds modules.

    PyInstaller stores core bootstrap modules (encodings, codecs, etc.) in
    base_library.zip, but the bare python.exe does not know to look there.
    A ._pth file is the lowest-level mechanism to configure sys.path and is
    processed before PYTHONPATH or PYTHONHOME.
    """
    internal_dir = output_dir / "_internal"
    ver = f"{sys.version_info.major}{sys.version_info.minor}"
    pth_name = f"python{ver}._pth"
    pth_path = internal_dir / pth_name

    if pth_path.exists():
        content = pth_path.read_text(encoding="utf-8")
        if "base_library.zip" in content:
            print(f"  [OK] {pth_name} already configured")
            return

    lines = []
    if (internal_dir / "base_library.zip").exists():
        lines.append("base_library.zip")
    zip_name = f"python{ver}.zip"
    if (internal_dir / zip_name).exists():
        lines.append(zip_name)
    lines.append(".")
    if (internal_dir / "Lib").is_dir():
        lines.append("Lib")
    if (internal_dir / "DLLs").is_dir():
        lines.append("DLLs")
    lines.append("import site")
    pth_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  [OK] Created {pth_name} with entries: {lines}")


def _bundled_python_env(internal_dir: Path) -> dict:
    """Build environment dict for invoking standalone _internal/python.exe."""
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV"):
        env.pop(key, None)

    # Windows onedir python.exe often needs explicit path hints so stdlib bootstrap
    # modules (runpy/importlib) can be resolved from bundled files.
    if sys.platform == "win32":
        parts = []
        base_lib = internal_dir / "base_library.zip"
        if base_lib.exists():
            parts.append(str(base_lib))
        py_zip = internal_dir / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
        if py_zip.exists():
            parts.append(str(py_zip))
        parts.append(str(internal_dir))
        lib = internal_dir / "Lib"
        if lib.is_dir():
            parts.append(str(lib))
        dlls = internal_dir / "DLLs"
        if dlls.is_dir():
            parts.append(str(dlls))
        env["PYTHONPATH"] = os.pathsep.join(parts)

    # Let bundled interpreter decide its own stdlib path layout.
    # Forcing PYTHONHOME/PYTHONPATH may break importlib on Linux/macOS bundles.
    env["PYTHONNOUSERSITE"] = "1"
    return env


def verify_bundled_python_contract(output_dir: Path) -> None:
    """Verify Contract A: onedir backend must include _internal/python* with pip."""
    internal_dir = output_dir / "_internal"
    if sys.platform == "win32":
        candidates = [internal_dir / "python.exe"]
    else:
        candidates = [internal_dir / "python3", internal_dir / "python"]

    py_path = next((p for p in candidates if p.exists()), None)
    if py_path is None:
        expected = ", ".join(str(p) for p in candidates)
        print(f"  [ERROR] Bundled Python missing. Expected one of: {expected}")
        sys.exit(1)

    print(f"  [OK] Bundled Python found: {py_path}")
    env = _bundled_python_env(internal_dir)
    try:
        result = subprocess.run(
            [str(py_path), "-c", "import pip; print(pip.__version__)"],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
    except Exception as exc:
        print(f"  [ERROR] Failed to run bundled Python pip check: {exc}")
        sys.exit(1)

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        print(f"  [ERROR] Bundled Python pip check failed (exit {result.returncode})")
        if stderr:
            print(f"    stderr: {stderr[:500]}")
        sys.exit(1)
    pip_ver = (result.stdout or "").strip()
    print(f"  [OK] Bundled Python pip check passed (pip {pip_ver})")


def create_stdlib_zip(output_dir: Path) -> Path | None:
    """Create python3XX.zip containing stdlib for the standalone interpreter.

    PyInstaller stores collected pure-Python modules in a PYZ archive that is
    only accessible through its own bootloader (openakita-server.exe).  The
    standalone python.exe in _internal/ cannot read the PYZ, so it has no
    access to stdlib modules like __future__, pathlib, etc.

    This function follows the same pattern as Python's official Windows
    embedded distribution: a zip file (python3XX.zip) containing the stdlib,
    referenced by the ._pth file so the interpreter can import from it.
    """
    internal_dir = output_dir / "_internal"
    ver = f"{sys.version_info.major}{sys.version_info.minor}"
    zip_name = f"python{ver}.zip"
    zip_path = internal_dir / zip_name

    if zip_path.exists():
        print(f"  [OK] {zip_name} already exists, skipping creation")
        return zip_path

    stdlib_src = Path(os.__file__).parent
    if not stdlib_src.is_dir():
        print(f"  [WARN] Cannot locate stdlib source at {stdlib_src}")
        return None

    import zipfile

    skip_dirs = {
        "test", "tests", "idlelib", "tkinter", "turtledemo", "turtle",
        "lib2to3", "ensurepip", "distutils", "pydoc_data",
        "__pycache__", "site-packages",
    }
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(stdlib_src):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                full = Path(root) / fname
                arcname = str(full.relative_to(stdlib_src))
                zf.write(full, arcname)
                count += 1

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"  [OK] Created {zip_name}: {count} modules, {size_mb:.1f} MB")
    return zip_path


def normalize_macos_bundled_python(output_dir: Path) -> None:
    """Normalize macOS bundled python entrypoint to framework interpreter.

    On macOS, ensure a valid fallback entrypoint exists when framework assets are
    present. Keep the original bundled launcher intact to avoid changing Python's
    expected startup behavior.
    """
    if sys.platform != "darwin":
        return

    internal_dir = output_dir / "_internal"
    wanted = f"{sys.version_info.major}.{sys.version_info.minor}"

    app_in_resources = internal_dir / "Resources" / "Python.app"
    app_in_framework = internal_dir / "Python.framework" / "Versions" / wanted / "Resources" / "Python.app"

    # Keep both common launcher-relative layouts valid:
    # - _internal/Resources/Python.app
    # - _internal/Python.framework/Versions/<ver>/Resources/Python.app
    if app_in_resources.exists() and not app_in_framework.exists():
        app_in_framework.parent.mkdir(parents=True, exist_ok=True)
        rel = os.path.relpath(app_in_resources, app_in_framework.parent)
        app_in_framework.symlink_to(rel)
        print(f"  [OK] Added framework-path Python.app symlink -> {app_in_resources}")
    elif app_in_framework.exists() and not app_in_resources.exists():
        app_in_resources.parent.mkdir(parents=True, exist_ok=True)
        rel = os.path.relpath(app_in_framework, app_in_resources.parent)
        app_in_resources.symlink_to(rel)
        print(f"  [OK] Added resources-path Python.app symlink -> {app_in_framework}")

    framework_candidates = sorted(
        internal_dir.glob("Python.framework/Versions/*/Resources/Python.app/Contents/MacOS/Python")
    )
    resources_candidate = internal_dir / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    candidates = framework_candidates + ([resources_candidate] if resources_candidate.exists() else [])
    if not candidates:
        print("  [WARN] macOS Python.app interpreter not found; skip python entrypoint normalization")
        return

    # Prefer framework layout with matching major.minor; fallback to first existing candidate.
    target = candidates[0]
    for cand in framework_candidates:
        if f"/Versions/{wanted}/" in str(cand):
            target = cand
            break

    try:
        target.chmod(target.stat().st_mode | 0o111)
    except Exception:
        # Keep best effort; verification step will catch non-executable targets.
        pass

    rel_target = os.path.relpath(target, internal_dir)
    touched = False
    for entry_name in ("python3", "python"):
        entry = internal_dir / entry_name
        # Do not overwrite existing launcher binaries; only fill missing entrypoints.
        if entry.exists() or entry.is_symlink():
            continue
        entry.symlink_to(rel_target)
        touched = True

    if touched:
        print(f"  [OK] Added fallback macOS bundled python entrypoint -> {target}")
    else:
        print("  [OK] Existing macOS bundled python launcher preserved")


def _build_web_frontend(web_src: Path, web_dist: Path) -> None:
    """Build the web frontend (dist-web/) so packaged assets are always fresh."""
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    pkg_json = web_src / "package.json"
    if not pkg_json.exists():
        print(f"  [WARN] {pkg_json} not found, skipping web build")
        return

    node_modules = web_src / "node_modules"
    if not node_modules.exists():
        print("  Installing npm dependencies...")
        run_cmd([npm, "install"], cwd=str(web_src))

    print("  Building web frontend (build:web)...")
    run_cmd([npm, "run", "build:web"], cwd=str(web_src))


def check_pyinstaller():
    """Check if PyInstaller is installed"""
    try:
        import PyInstaller  # noqa: F401
        print(f"  [OK] PyInstaller {PyInstaller.__version__} installed")
    except ImportError:
        print("  [WARN] PyInstaller not installed, installing...")
        run_cmd([sys.executable, "-m", "pip", "install", "pyinstaller"])


def clean_dist():
    """Clean previous build output"""
    # Clean dist output directory
    if OUTPUT_DIR.exists():
        print(f"  Cleaning old build output: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)

    # Clean entire dist directory to avoid symlink conflicts on macOS
    if DIST_DIR.exists():
        print(f"  Cleaning dist directory: {DIST_DIR}")
        shutil.rmtree(DIST_DIR)

    # Clean build temp directory
    build_tmp = PROJECT_ROOT / "build" / "openakita-server"
    if build_tmp.exists():
        shutil.rmtree(build_tmp)

    # Clean PyInstaller work directory (fixes macOS symlink FileExistsError)
    pyinstaller_work = PROJECT_ROOT / "build" / "pyinstaller_work"
    if pyinstaller_work.exists():
        print(f"  Cleaning PyInstaller work directory: {pyinstaller_work}")
        shutil.rmtree(pyinstaller_work)

    # Clean legacy PyInstaller analysis cache (from builds that used default workpath)
    legacy_cache = PROJECT_ROOT / "build" / "openakita"
    if legacy_cache.exists():
        print(f"  Cleaning legacy PyInstaller cache: {legacy_cache}")
        shutil.rmtree(legacy_cache)


def ensure_playwright_chromium():
    """Ensure Playwright Chromium is installed for bundling"""
    try:
        import playwright
        pw_dir = Path(playwright.__file__).parent
        local_browsers = pw_dir / ".local-browsers"
        if local_browsers.exists():
            print("  [OK] Playwright Chromium already installed (local-browsers)")
            return

        # Check default system path
        if sys.platform == "win32":
            pw_cache = Path.home() / "AppData" / "Local" / "ms-playwright"
        elif sys.platform == "darwin":
            pw_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
        else:
            pw_cache = Path.home() / ".cache" / "ms-playwright"

        chromium_found = False
        if pw_cache.exists():
            for d in pw_cache.iterdir():
                if d.is_dir() and "chromium" in d.name.lower():
                    chromium_found = True
                    break

        if chromium_found:
            print(f"  [OK] Playwright Chromium found at {pw_cache}")
        else:
            print("  [INFO] Installing Playwright Chromium for bundling...")
            run_cmd([sys.executable, "-m", "playwright", "install", "chromium"])
    except ImportError:
        print("  [WARN] playwright not installed, installing...")
        run_cmd([sys.executable, "-m", "pip", "install", "playwright", "browser-use", "langchain-openai"])
        print("  [INFO] Installing Playwright Chromium...")
        run_cmd([sys.executable, "-m", "playwright", "install", "chromium"])


def build_backend(mode: str, fast: bool = False):
    """Execute PyInstaller packaging"""
    label = f"{mode.upper()}" + (" [FAST]" if fast else "")
    print(f"\n{'='*60}")
    print(f"  OpenAkita Backend Build - Mode: {label}")
    print(f"{'='*60}\n")

    print("[1/5] Checking dependencies...")
    check_pyinstaller()

    print("\n[2/5] Ensuring Playwright Chromium for bundling...")
    ensure_playwright_chromium()

    print("\n[3/5] Cleaning old build...")
    clean_dist()

    print("\n[4/5] Running PyInstaller...")
    env = {"OPENAKITA_BUILD_MODE": mode}
    if fast:
        env["OPENAKITA_NO_UPX"] = "1"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(SPEC_FILE),
        "--distpath", str(DIST_DIR),
        "--workpath", str(PROJECT_ROOT / "build" / "pyinstaller_work"),
        "--noconfirm",
    ]
    if not fast:
        cmd.append("--clean")

    run_cmd(cmd, env=env)

    print("\n[5/5] Verifying build output...")
    
    if sys.platform == "win32":
        exe_path = OUTPUT_DIR / "openakita-server.exe"
    else:
        exe_path = OUTPUT_DIR / "openakita-server"

    if not exe_path.exists():
        print(f"  [ERROR] Executable not found: {exe_path}")
        sys.exit(1)

    # Test executable
    try:
        result = subprocess.run(
            [str(exe_path), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(f"  [OK] Executable verified: {exe_path}")
        else:
            print(f"  [WARN] Executable returned non-zero exit code: {result.returncode}")
            print(f"    stderr: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        print("  [WARN] Executable timed out (may be normal, continuing)")
    except Exception as e:
        print(f"  [WARN] Exception during verification: {e}")

    normalize_macos_bundled_python(OUTPUT_DIR)
    create_stdlib_zip(OUTPUT_DIR)
    ensure_bundled_pth_file(OUTPUT_DIR)
    verify_bundled_python_contract(OUTPUT_DIR)

    # Build & include web frontend
    web_src = PROJECT_ROOT / "apps" / "setup-center"
    web_dist = web_src / "dist-web"
    _build_web_frontend(web_src, web_dist)
    if web_dist.exists() and (web_dist / "index.html").exists():
        web_dest = OUTPUT_DIR / "_internal" / "openakita" / "web"
        if web_dest.exists():
            shutil.rmtree(web_dest)
        shutil.copytree(web_dist, web_dest)
        web_files = sum(1 for _ in web_dest.rglob("*") if _.is_file())
        print(f"  [OK] Web frontend included ({web_files} files)")
    else:
        print("  [WARN] Web frontend not found after build attempt")

    # Calculate size
    total_size = sum(f.stat().st_size for f in OUTPUT_DIR.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)
    print(f"\n  Build completed!")
    print(f"  Output directory: {OUTPUT_DIR}")
    print(f"  Total size: {size_mb:.1f} MB")
    print(f"  Mode: {mode.upper()}")


def main():
    parser = argparse.ArgumentParser(description="OpenAkita backend build script")
    parser.add_argument(
        "--mode",
        choices=["core", "full"],
        default="core",
        help="Build mode: core=minimal(exclude heavy deps), full=complete(all deps)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast build: skip UPX compression and incremental build (no --clean)",
    )
    args = parser.parse_args()
    build_backend(args.mode, fast=args.fast)


if __name__ == "__main__":
    main()
