"""
Runtime environment detection — compatible with both PyInstaller bundles and regular Python environments.

When frozen by PyInstaller, sys.executable points to openakita-server.exe rather than the Python interpreter.
This module provides a unified runtime-environment detection layer so that pip install, script execution,
and similar features keep working.
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

IS_FROZEN = getattr(sys, "frozen", False)
"""Whether we are running inside a PyInstaller bundle."""


def _find_python_in_dir(directory: Path) -> Path | None:
    """Look for a Python executable inside the given directory."""
    if sys.platform == "win32":
        candidates = ["python.exe", "python3.exe"]
    else:
        candidates = ["python3", "python"]

    for name in candidates:
        py = directory / name
        if py.exists():
            return py
    # Also check the bin/ and Scripts/ subdirectories
    for sub in ("bin", "Scripts"):
        sub_dir = directory / sub
        if sub_dir.is_dir():
            for name in candidates:
                py = sub_dir / name
                if py.exists():
                    return py
    return None


def _is_windows_store_stub(path: str) -> bool:
    """Quick check for the Windows Store redirect stub (App Execution Alias).

    AppInstallerPythonRedirector is Microsoft's fake stub that prompts users to install
    Python; at runtime it returns exit code 9009 and is not a real Python.
    Note: the WindowsApps directory may also contain a real Microsoft Store Python
    installation, so we cannot exclude purely by path — must verify via
    verify_python_executable().
    """
    return "AppInstallerPythonRedirector" in path


def verify_python_executable(path: str) -> bool:
    """Verify that a Python executable is actually usable.

    Runs ``python --version`` and confirms the return code is 0 and the output starts
    with ``Python 3.``. This filters out Windows Store stubs (exit 9009), broken
    installs, and legacy non-Python-3 versions.
    """
    import subprocess

    try:
        kwargs: dict = {"capture_output": True, "text": True, "timeout": 5}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run([path, "--version"], **kwargs)
        if result.returncode != 0:
            logger.debug("Python verification failed (exit %d): %s", result.returncode, path)
            return False
        output = (result.stdout + result.stderr).strip()
        if output.startswith("Python 3."):
            logger.debug("Python verified: %s -> %s", path, output)
            return True
        logger.debug("Python version mismatch (requires 3.x): %s -> %s", path, output)
        return False
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as exc:
        logger.debug("Python verification error: %s -> %s", path, exc)
        return False


# NOTE: _which_real_python / _scan_common_python_dirs / _get_python_from_env_var
# have been removed — we no longer search the user's system Python and only use
# Python that ships with the project or is installed by the project itself.
# This eliminates conflicts caused by user Anaconda installs, Windows Store stubs,
# version mismatches, etc.


def get_configured_venv_path() -> str | None:
    """Get the virtual environment path (used by prompt building and similar modules).

    Priority: inferred from the current Python interpreter path.
    """
    if not IS_FROZEN:
        if sys.prefix != sys.base_prefix:
            return sys.prefix
        return None

    py = get_python_executable()
    if not py:
        return None
    py_path = Path(py)
    # Scripts/python.exe -> venv root, or bin/python -> venv root
    if py_path.parent.name in ("Scripts", "bin"):
        venv_root = py_path.parent.parent
        pyvenv_cfg = venv_root / "pyvenv.cfg"
        if pyvenv_cfg.exists():
            return str(venv_root)
    return None


def _get_openakita_root() -> Path:
    """Get the OpenAkita root directory path (avoids a circular import of config).

    Prefers the OPENAKITA_ROOT environment variable; defaults to ~/.openakita.
    """
    import os

    env_root = os.environ.get("OPENAKITA_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    return Path.home() / ".openakita"


def _get_bundled_internal_python() -> str | None:
    """Locate the Python interpreter bundled in the _internal/ directory by PyInstaller.

    At build time, openakita.spec copies sys.executable and pip into _internal/, so
    this Python version matches the build environment exactly and will not cause
    compatibility issues.
    """
    if not IS_FROZEN:
        return None
    exe_dir = Path(sys.executable).parent
    internal_dir = exe_dir if exe_dir.name == "_internal" else exe_dir / "_internal"
    if not internal_dir.is_dir():
        return None
    if sys.platform == "win32":
        candidates = ["python.exe", "python3.exe"]
    else:
        candidates = ["python3", "python"]
    for name in candidates:
        py = internal_dir / name
        if py.exists() and verify_python_executable(str(py)):
            logger.debug("Using bundled Python (_internal): %s", py)
            return str(py)
    return None


def get_python_executable() -> str | None:
    """Return an available Python interpreter path.

    **Only uses Python that ships with the project or is installed by the project;
    never uses the user's system Python.**

    Lookup order in a PyInstaller environment:
      1. Workspace venv ({project_root}/data/venv/)
      2. Global venv (~/.openakita/venv/)
      3. Bundled Python (_internal/python.exe)

    In a regular development environment: returns sys.executable.
    """
    if not IS_FROZEN:
        return sys.executable

    # 1. Check {project_root}/data/venv/ — workspace virtual environment
    try:
        from .config import settings

        workspace_venv = settings.project_root / "data" / "venv"
        py = _find_python_in_dir(workspace_venv)
        if py and verify_python_executable(str(py)):
            logger.debug(f"Using workspace venv Python: {py}")
            return str(py)
        elif py:
            logger.warning(f"Workspace venv Python exists but failed verification, skipping: {py}")
    except Exception:
        pass

    root = _get_openakita_root()

    # 2. Check ~/.openakita/venv/
    if sys.platform == "win32":
        venv_python = root / "venv" / "Scripts" / "python.exe"
    else:
        venv_python = root / "venv" / "bin" / "python"
    if venv_python.exists():
        if verify_python_executable(str(venv_python)):
            logger.debug(f"Using venv Python: {venv_python}")
            return str(venv_python)
        else:
            logger.warning(f"Global venv Python failed verification, skipping: {venv_python}")

    # 3. Bundled Python (in _internal/; same-version Python + pip bundled at build time)
    bundled = _get_bundled_internal_python()
    if bundled:
        return bundled

    logger.warning(
        "No project-bundled Python interpreter found. "
        "Searched: workspace venv -> ~/.openakita/venv -> "
        "bundled Python. "
        "Please reinstall OpenAkita and make sure the installer resources are complete."
    )
    return None


def can_pip_install() -> bool:
    """Check whether the current environment supports pip install."""
    py = get_python_executable()
    if not py:
        return False
    # PyInstaller bundles need an external Python to run pip install
    if IS_FROZEN:
        return py != sys.executable
    return True


_DEFAULT_PIP_INDEX = "https://mirrors.aliyun.com/pypi/simple/"
_DEFAULT_PIP_TRUSTED_HOST = "mirrors.aliyun.com"


def get_pip_command(packages: list[str], *, index_url: str | None = None) -> list[str] | None:
    """Build a pip install command list (defaults to a domestic mirror).

    Args:
        packages: List of package names to install
        index_url: Custom mirror URL; when None, uses the Aliyun mirror

    Returns:
        Command argument list, or None if not supported.
    """
    import os

    py = get_python_executable()
    if not py:
        return None
    if IS_FROZEN and py == sys.executable:
        return None

    effective_index = os.environ.get("PIP_INDEX_URL", "").strip() or index_url or _DEFAULT_PIP_INDEX
    trusted_host = effective_index.split("//")[1].split("/")[0] if "//" in effective_index else ""

    return [
        py,
        "-m",
        "pip",
        "install",
        "-i",
        effective_index,
        "--trusted-host",
        trusted_host,
        "--prefer-binary",
        *packages,
    ]


def get_channel_deps_dir() -> Path:
    """Return the isolated install directory for IM channel dependencies.

    Path: ~/.openakita/modules/channel-deps/site-packages
    This directory is automatically scanned and injected into sys.path by
    inject_module_paths().
    """
    return _get_openakita_root() / "modules" / "channel-deps" / "site-packages"


def ensure_ssl_certs() -> None:
    """Ensure an SSL certificate bundle is available in a PyInstaller environment.

    httpx uses trust_env=True by default and prefers the SSL_CERT_FILE environment
    variable. Conda/Anaconda installs set SSL_CERT_FILE in the system environment to
    Conda's own cacert.pem (e.g. Anaconda3/Library/ssl/cacert.pem), but in non-Conda
    environments that path does not exist and httpx raises
    FileNotFoundError: [Errno 2] No such file or directory when creating an SSL context.

    This function detects and repairs SSL_CERT_FILE so it points to a certificate file
    that actually exists.
    """
    if not IS_FROZEN:
        return

    import os

    # If SSL_CERT_FILE is already set and the file really exists, nothing to do
    existing = os.environ.get("SSL_CERT_FILE", "").strip()
    if existing and Path(existing).is_file():
        return

    if existing:
        logger.warning(
            f"SSL_CERT_FILE points to non-existent file: {existing} "
            f"(likely set by Conda/Anaconda). Overriding with bundled CA bundle."
        )

    # Option 1: certifi module is available and its path is valid
    try:
        import certifi

        pem_path = certifi.where()
        if Path(pem_path).is_file():
            os.environ["SSL_CERT_FILE"] = pem_path
            logger.info(f"SSL_CERT_FILE set from certifi: {pem_path}")
            return
    except ImportError:
        pass

    # Option 2: look inside the PyInstaller _internal/ directory
    internal_dir = Path(sys.executable).parent
    if internal_dir.name != "_internal":
        internal_dir = internal_dir / "_internal"

    for candidate in [
        internal_dir / "certifi" / "cacert.pem",
        internal_dir / "certifi" / "cert.pem",
    ]:
        if candidate.is_file():
            os.environ["SSL_CERT_FILE"] = str(candidate)
            logger.info(f"SSL_CERT_FILE set from bundled path: {candidate}")
            return

    # Option 3: clear an invalid SSL_CERT_FILE so httpx falls back to certifi.where()
    if existing:
        del os.environ["SSL_CERT_FILE"]
        logger.warning("Removed invalid SSL_CERT_FILE. httpx will fall back to certifi default.")
        return

    logger.warning(
        "SSL CA bundle not found in PyInstaller environment. "
        "HTTPS requests may fail with [Errno 2] No such file or directory."
    )


def _sanitize_sys_path() -> None:
    """Detect and remove externally leaked paths from sys.path (defense in depth).

    Even though the Tauri side clears harmful environment variables (such as
    PYTHONPATH) at startup, paths may still be injected via other mechanisms
    (.pth files, site-packages hooks, etc.). This function removes any
    site-packages directories that are not owned by the project, preventing
    packages from the user's Anaconda or system Python from overriding the
    bundled modules.
    """
    if not IS_FROZEN:
        return

    import os

    meipass = getattr(sys, "_MEIPASS", "")
    openakita_root = str(_get_openakita_root())

    suspicious = []
    for p in list(sys.path):
        if not p:
            continue
        # Allow: PyInstaller internal paths
        if meipass and p.startswith(meipass):
            continue
        # Allow: the project data directory (~/.openakita/)
        if p.startswith(openakita_root):
            continue
        # Allow: the current working directory ('' or '.')
        if p in ("", "."):
            continue
        # Allow: temp directory (some runtimes generate paths here dynamically)
        tmp = os.environ.get("TEMP", os.environ.get("TMPDIR", ""))
        if tmp and p.startswith(tmp):
            continue
        # Flag: external paths containing site-packages are a danger signal
        p_lower = p.lower().replace("\\", "/")
        if "site-packages" in p_lower or "dist-packages" in p_lower:
            suspicious.append(p)

    if suspicious:
        for p in suspicious:
            sys.path.remove(p)
        logger.warning(
            f"Removed {len(suspicious)} external site-packages path(s) "
            f"(likely from the user's Anaconda/system Python): {suspicious[:5]}"
        )


def inject_module_paths() -> None:
    """Inject the site-packages directories of optional modules into sys.path.

    Path sources (in priority order):
    1. The OPENAKITA_MODULE_PATHS environment variable — the Tauri side uses
       this variable to pass the paths of installed modules.
    2. Scanning ~/.openakita/modules/*/site-packages — fallback mechanism.

    Important: must use sys.path.append(), not insert(0)!
    In a PyInstaller environment, bundled modules (like pydantic) live under
    _MEIPASS/_internal and are at the front of sys.path. If external module
    paths are inserted at the front, an external pydantic can shadow the
    bundled version, and its C extension pydantic_core._pydantic_core is not
    compatible with the PyInstaller environment, causing the process to crash
    outright during import.

    Note: the Tauri side does not use PYTHONPATH to inject module paths, because
    Python automatically inserts PYTHONPATH at the very front of sys.path at
    startup, which cannot guarantee that bundled modules win.
    """
    if not IS_FROZEN:
        return

    # First scrub external path leaks, then inject project-owned paths
    _sanitize_sys_path()

    import os

    injected = []

    # Source 1: read from the OPENAKITA_MODULE_PATHS environment variable (set by Tauri)
    env_paths = os.environ.get("OPENAKITA_MODULE_PATHS", "")
    if env_paths:
        sep = ";" if sys.platform == "win32" else ":"
        for p in env_paths.split(sep):
            p = p.strip()
            if p and p not in sys.path:
                sys.path.append(p)
                injected.append(Path(p).parent.name)

    # Source 2: scan ~/.openakita/modules/*/site-packages (fallback)
    # Skip modules that are already bundled into the core package to avoid
    # conflicts between external older versions and the bundled version.
    _BUILTIN_MODULE_IDS = {"browser"}
    modules_base = _get_openakita_root() / "modules"
    if modules_base.exists():
        for module_dir in modules_base.iterdir():
            if not module_dir.is_dir():
                continue
            if module_dir.name in _BUILTIN_MODULE_IDS:
                continue
            sp = module_dir / "site-packages"
            if sp.is_dir() and str(sp) not in sys.path:
                sys.path.append(str(sp))
                injected.append(module_dir.name)

    if injected:
        logger.info(f"Injected module paths (appended to the end of sys.path): {', '.join(injected)}")

    # On Windows, register DLL search paths for modules that include C-extension DLLs
    # (such as torch). Python 3.8+ on Windows no longer uses sys.path for DLL
    # resolution; they must be registered explicitly via os.add_dll_directory(),
    # otherwise the dependent DLLs of PYDs like torch._C (c10.dll, torch_cpu.dll, etc.)
    # cannot be located, producing "ImportError: DLL load failed".
    if sys.platform == "win32":
        _register_dll_directories(os)


def _register_dll_directories(os_module) -> None:
    """Register DLL search paths on Windows for sys.path entries containing C-extension DLLs.

    Walk each path in sys.path, check for known DLL subdirectories (such as
    torch/lib/), and register them via os.add_dll_directory(). Also append
    the DLL path to the PATH environment variable as a fallback.
    """
    # Known packages that need DLL directory registration, and their DLL subpaths
    _DLL_SUBDIRS = [
        ("torch", "lib"),  # PyTorch: c10.dll, torch_cpu.dll, libiomp5md.dll
        ("torch", "bin"),  # Some PyTorch versions place DLLs under bin/
    ]

    registered = []
    for p in list(sys.path):
        p_path = Path(p)
        if not p_path.is_dir():
            continue
        for pkg, sub in _DLL_SUBDIRS:
            dll_dir = p_path / pkg / sub
            if dll_dir.is_dir():
                dll_str = str(dll_dir)
                try:
                    os_module.add_dll_directory(dll_str)
                    registered.append(dll_str)
                except OSError as e:
                    logger.warning(f"Failed to add DLL path: {dll_dir} - {e}")
                # Fallback: append the DLL directory to PATH (for some older Python or special environments)
                current_path = os_module.environ.get("PATH", "")
                if dll_str not in current_path:
                    os_module.environ["PATH"] = dll_str + ";" + current_path

    if registered:
        logger.info(f"Registered Windows DLL search paths: {', '.join(registered)}")


def inject_module_paths_runtime() -> int:
    """Re-scan and inject module paths at runtime (does not require IS_FROZEN).

    Used so newly installed modules can be loaded without a restart.
    Unlike inject_module_paths(), this function does not check IS_FROZEN
    and can be called in any environment.

    Returns:
        The number of newly injected paths.
    """
    import os

    injected = []

    # Scan ~/.openakita/modules/*/site-packages
    modules_base = _get_openakita_root() / "modules"
    if modules_base.exists():
        for module_dir in modules_base.iterdir():
            if not module_dir.is_dir():
                continue
            sp = module_dir / "site-packages"
            if sp.is_dir() and str(sp) not in sys.path:
                sys.path.append(str(sp))
                injected.append(module_dir.name)

    if injected:
        logger.info(f"[Runtime] Injected module paths: {', '.join(injected)}")

    # Windows DLL directories
    if sys.platform == "win32":
        _register_dll_directories(os)

    return len(injected)
