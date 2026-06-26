"""
LLM 配置向导

提供多种方式配置 LLM 端点：
- CLI 命令行向导
- Web 配置页面
- Telegram 命令
"""

from .cli import quick_add_endpoint, run_cli_wizard

__all__ = [
    "run_cli_wizard",
    "quick_add_endpoint",
]
