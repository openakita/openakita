#!/usr/bin/env python3
"""Verify Contract A for packaged backend resources.

Contract A requires on all platforms:
1) backend executable exists in openakita-server/
2) bundled interpreter exists in openakita-server/_internal/python*
3) bundled interpreter can import pip
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _bundled_python_env(internal_dir: Path) -> dict:
    """Build environment dict for invoking standalone _internal/python.exe."""
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV"):
        env.pop(key, None)

    # NOTE: When python3XX._pth exists (created by build_backend.py), Python
    # ignores PYTHONPATH entirely.  The ._pth file already references
    # python3XX.zip (stdlib) and base_library.zip.  We still set PYTHONPATH
    # here as a fallback for environments where ._pth may not yet exist.
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


def _major_minor(version: str) -> str:
    match = re.match(r"^\s*(\d+)\.(\d+)", version)
    if not match:
        return ""
    return f"{match.group(1)}.{match.group(2)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify bundled Python contract")
    parser.add_argument(
        "--backend-dir",
        required=True,
        help="Path to openakita-server directory",
    )
    parser.add_argument(
        "--bootstrap-manifest",
        default="",
        help="Optional bootstrap manifest used to verify CPython ABI consistency",
    )
    parser.add_argument(
        "--require-seed-packaged",
        action="store_true",
        help=(
            "Additionally assert manifest.python_seed.packaged == true, "
            "verify seed Python can import essential stdlib modules, and "
            "verify the seed major.minor matches PyInstaller bundled Python. "
            "Use in CI/release packaging path."
        ),
    )
    args = parser.parse_args()

    backend_dir = Path(args.backend_dir).resolve()
    if not backend_dir.is_dir():
        print(f"[ERROR] backend dir not found: {backend_dir}")
        return 1

    exe = backend_dir / ("openakita-server.exe" if sys.platform == "win32" else "openakita-server")
    if not exe.exists():
        print(f"[ERROR] backend executable missing: {exe}")
        return 1
    print(f"[OK] backend executable: {exe}")

    internal = backend_dir / "_internal"
    if sys.platform == "win32":
        candidates = [internal / "python.exe"]
    else:
        candidates = [internal / "python3", internal / "python"]

    py = next((p for p in candidates if p.exists()), None)
    if py is None:
        print("[ERROR] bundled python missing; expected one of:")
        for c in candidates:
            print(f"  - {c}")
        return 1
    print(f"[OK] bundled python: {py}")

    env = _bundled_python_env(internal)
    try:
        result = subprocess.run(
            [str(py), "-c", "import pip; print(pip.__version__)"],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
    except Exception as exc:
        print(f"[ERROR] failed to execute bundled python: {exc}")
        return 1

    if result.returncode != 0:
        print(f"[ERROR] bundled pip check failed (exit {result.returncode})")
        stderr = (result.stderr or "").strip()
        if stderr:
            print(stderr[:500])
        return 1
    pip_ver = (result.stdout or "").strip()
    print(f"[OK] bundled pip check passed (pip {pip_ver})")

    version_check = subprocess.run(
        [str(py), "-c", "import platform,sysconfig; print(platform.python_version()); print(sysconfig.get_config_var('SOABI') or '')"],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    if version_check.returncode != 0:
        print("[ERROR] bundled Python version probe failed")
        print((version_check.stderr or version_check.stdout or "").strip()[:500])
        return 1
    version_lines = [line.strip() for line in version_check.stdout.splitlines()]
    bundled_version = version_lines[0] if version_lines else ""
    bundled_abi = version_lines[1] if len(version_lines) > 1 else ""
    print(f"[OK] bundled Python version: {bundled_version} ({bundled_abi or 'no SOABI'})")

    manifest: dict | None = None
    if args.bootstrap_manifest:
        manifest_path = Path(args.bootstrap_manifest).resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = str(manifest.get("python_version") or "")
        bundled_major_minor = _major_minor(bundled_version)
        expected_major_minor = _major_minor(expected)
        if expected_major_minor and bundled_major_minor != expected_major_minor:
            print(
                f"[ERROR] bootstrap python_version {expected!r} does not match "
                f"bundled Python {bundled_version!r}"
            )
            return 1
        expected_abi = str(manifest.get("python_abi") or "")
        if expected_abi and bundled_abi:
            print(f"[OK] bootstrap manifest ABI: {expected_abi}; bundled SOABI: {bundled_abi}")
        print("[OK] bootstrap manifest Python ABI matches bundled backend")

    if args.require_seed_packaged:
        if manifest is None:
            print("[ERROR] --require-seed-packaged requires --bootstrap-manifest")
            return 1
        seed = manifest.get("python_seed") or {}
        if not seed.get("packaged"):
            print("[ERROR] python_seed.packaged is not true; CI/release must bundle seed")
            return 1
        bootstrap_dir = manifest_path.parent
        seed_path = bootstrap_dir / seed.get("path", "")
        if not seed_path.is_file():
            print(f"[ERROR] python_seed binary missing on disk: {seed_path}")
            return 1
        print(f"[OK] python_seed binary present: {seed_path}")

        if os.name != "nt" and not os.access(seed_path, os.X_OK):
            print(f"[ERROR] python_seed binary missing executable bit (0o755): {seed_path}")
            return 1
        if os.name != "nt":
            print("[OK] python_seed binary has executable bit set")

        # Seed stdlib smoke (host must be able to exec target; CI does this
        # by running matrix on the corresponding runner, so we can attempt).
        try:
            seed_env = _bundled_python_env(seed_path.parent)
            seed_env.pop("PYTHONPATH", None)  # PBS seed has its own layout
            seed_check = subprocess.run(
                [
                    str(seed_path),
                    "-c",
                    "import ssl, ctypes, zlib, sqlite3, hashlib, json, platform; "
                    "print(platform.python_version())",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=seed_env,
            )
        except Exception as exc:
            print(f"[ERROR] failed to exec python_seed: {exc}")
            return 1
        if seed_check.returncode != 0:
            print(f"[ERROR] python_seed stdlib smoke failed (exit {seed_check.returncode})")
            print((seed_check.stderr or seed_check.stdout or "").strip()[:500])
            return 1
        seed_version = (seed_check.stdout or "").strip()
        print(f"[OK] python_seed stdlib smoke passed (Python {seed_version})")

        seed_major_minor = _major_minor(seed_version)
        bundled_major_minor = _major_minor(bundled_version)
        if seed_major_minor and bundled_major_minor and seed_major_minor != bundled_major_minor:
            print(
                f"[ERROR] python_seed major.minor {seed_major_minor!r} does not match "
                f"PyInstaller bundled {bundled_major_minor!r}"
            )
            return 1
        print(
            f"[OK] python_seed {seed_version} matches PyInstaller bundled {bundled_version} (major.minor)"
        )

        # Confirm slim worked: Lib/test must NOT exist (Windows) /
        # lib/python3.X/test must NOT exist (POSIX).
        slim_violations: list[Path] = []
        if os.name == "nt":
            candidate_lib_roots = [seed_path.parent / "Lib"]
        else:
            lib_base = seed_path.parent.parent / "lib"
            candidate_lib_roots = list(lib_base.glob("python3.*")) if lib_base.is_dir() else []
        for lib_root in candidate_lib_roots:
            for sub in ("test", "idlelib", "tkinter", "turtledemo"):
                bad = lib_root / sub
                if bad.exists():
                    slim_violations.append(bad)
        if slim_violations:
            print("[ERROR] python_seed slim verification failed; these should be removed:")
            for v in slim_violations:
                print(f"  - {v}")
            return 1
        print("[OK] python_seed slim verified (no test/idlelib/tkinter/turtledemo)")

    if sys.platform.startswith("linux"):
        for rel in ("libpython", "Python.framework"):
            hits = [p for p in internal.rglob("*") if rel in p.name]
            if hits:
                print(f"[OK] Linux bundled native runtime artifacts: {len(hits)} {rel} matches")
                break
        else:
            print("[WARN] Linux libpython artifact not found under _internal; verify PyInstaller bundle manually")

    if sys.platform == "darwin":
        framework = internal / "Python.framework"
        if not framework.exists():
            print("[WARN] macOS Python.framework not found under _internal; notarization may still pass for non-framework builds")
        else:
            print(f"[OK] macOS Python.framework present: {framework}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
