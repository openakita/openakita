"""
OpenAkita - 全能自进化AI Agent

基于 Ralph Wiggum 模式，永不放弃。
"""


def _resolve_version_info() -> tuple[str, str]:
    """
    解析版本号和 git 短哈希。

    返回 (version, git_hash)。
    打包模式下 _bundled_version.txt 格式为 "1.22.7+823f46b"。
    开发模式下自动从 git 获取当前 HEAD 短哈希。
    """
    from pathlib import Path

    version = "0.0.0-dev"
    git_hash = "unknown"

    # 1. PyInstaller 打包模式：读取构建时写入的版本文件（格式: "1.22.7+abc1234"）
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

    # 2. 尝试读取源码根目录的 pyproject.toml（editable 安装时始终最新）
    project_root = Path(__file__).parent.parent.parent
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            import tomllib

            with open(pyproject_path, "rb") as f:
                version = tomllib.load(f)["project"]["version"]
        except Exception:
            pass

    # 3. 回退到已安装包的元数据
    if version == "0.0.0-dev":
        try:
            from importlib.metadata import version as meta_version

            version = meta_version("openakita")
        except Exception:
            pass

    # 开发模式下从 git 获取当前哈希
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
    """返回完整版本标识，如 '1.22.7+823f46b'"""
    return f"{__version__}+{__git_hash__}"


__author__ = "OpenAkita"
