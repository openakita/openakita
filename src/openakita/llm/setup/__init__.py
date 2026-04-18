"""
LLM configuration wizard

Provides multiple ways to configure LLM endpoints:
- CLI interactive wizard
- Web configuration page
- Telegram commands
"""

from .cli import quick_add_endpoint, run_cli_wizard

__all__ = [
    "run_cli_wizard",
    "quick_add_endpoint",
]
