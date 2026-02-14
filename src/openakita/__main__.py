"""
OpenAkita 包入口点 - 支持 `python -m openakita` 调用
同时作为 PyInstaller 打包入口
"""

from openakita.main import app

if __name__ == "__main__":
    app()
