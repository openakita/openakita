"""
OpenAkita - 全能自进化AI Agent

基于 Ralph Wiggum 模式，永不放弃。
"""

def _resolve_version() -> str:
    """
    解析版本号。
    优先从 pyproject.toml 读取（editable 安装时始终最新），
    回退到 importlib.metadata（正式 pip install 后可用）。
    """
    from pathlib import Path

    # 1. 尝试读取源码根目录的 pyproject.toml（editable 模式下始终最新）
    pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
    if pyproject_path.exists():
        try:
            import tomllib
            with open(pyproject_path, "rb") as f:
                return tomllib.load(f)["project"]["version"]
        except Exception:
            pass

    # 2. 回退到已安装包的元数据
    try:
        from importlib.metadata import version
        return version("openakita")
    except Exception:
        pass

    return "0.0.0-dev"


__version__ = _resolve_version()

__author__ = "OpenAkita"
