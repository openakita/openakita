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


def build_backend(mode: str):
    """Execute PyInstaller packaging"""
    print(f"\n{'='*60}")
    print(f"  OpenAkita Backend Build - Mode: {mode.upper()}")
    print(f"{'='*60}\n")

    print("[1/4] Checking dependencies...")
    check_pyinstaller()

    print("\n[2/4] Cleaning old build...")
    clean_dist()

    print("\n[3/4] Running PyInstaller...")
    env = {"OPENAKITA_BUILD_MODE": mode}
    
    # On macOS, set environment to avoid symlink issues
    if sys.platform == "darwin":
        # Disable symlinks in PyInstaller to avoid FileExistsError
        env["PYINSTALLER_DO_NOT_SYMLINK_PYTHON"] = "1"
    
    run_cmd(
        [
            sys.executable, "-m", "PyInstaller",
            str(SPEC_FILE),
            "--distpath", str(DIST_DIR),
            "--workpath", str(PROJECT_ROOT / "build" / "pyinstaller_work"),
            "--noconfirm",
            "--clean",  # Force clean build to avoid symlink conflicts on macOS
        ],
        env=env,
    )

    print("\n[4/4] Verifying build output...")
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
    args = parser.parse_args()
    build_backend(args.mode)


if __name__ == "__main__":
    main()
