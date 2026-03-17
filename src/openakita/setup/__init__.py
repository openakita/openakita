"""
OpenAkita 安装向导模块
"""

__all__ = ["SetupWizard"]


def __getattr__(name: str):
    if name == "SetupWizard":
        from .wizard import SetupWizard
        return SetupWizard
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
