"""
OpenAkita - Versatile Self-Evolving AI Agent

Based on the Ralph Wiggum pattern — never gives up.
"""


def _resolve_version_info() -> tuple[str, str]:
    """
    Resolve the version number and git short hash.

    Returns (version, git_hash).
    In packaged mode, _bundled_version.txt format is "1.22.7+823f46b".
    In development mode, the current HEAD short hash is fetched from git.
    """
    from pathlib import Path

    version = "0.0.0-dev"
    git_hash = "unknown"

    # 1. PyInstaller packaged mode: read version file written at build time (format: "1.22.7+abc1234")
    bundled_ver = Path(__file__).parent / "_bundled_version.txt"
    if bundled_ver.exists():
        try:
            raw = bundled_ver.read_text(encoding="utf-8").strip()
            if "+" in raw:
                version, git_hash = raw.split("+", 1)
            else:
                version = raw
            return version, git_hash
        except Exception:
            pass

    # 2. Try reading pyproject.toml from the source root (always up-to-date in editable installs)
    project_root = Path(__file__).parent.parent.parent
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            import tomllib

            with open(pyproject_path, "rb") as f:
                version = tomllib.load(f)["project"]["version"]
        except Exception:
            pass

    # 3. Fall back to installed package metadata
    if version == "0.0.0-dev":
        try:
            from importlib.metadata import version as meta_version

            version = meta_version("openakita")
        except Exception:
            pass

    # In development mode, get the current hash from git
    try:
        import subprocess

        git_hash = subprocess.check_output(
            ["git", "-C", str(project_root), "rev-parse", "--short=7", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        ).strip()
    except Exception:
        git_hash = "dev"

    return version, git_hash


__version__, __git_hash__ = _resolve_version_info()


def get_version_string() -> str:
    """Return the full version identifier, e.g. '1.22.7+823f46b'."""
    return f"{__version__}+{__git_hash__}"


__author__ = "OpenAkita"
