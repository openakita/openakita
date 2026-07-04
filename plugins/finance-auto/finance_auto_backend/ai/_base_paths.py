"""Centralised path constants for the AI module.

Keeping the prompt-template directory lookup in one place avoids every
scenario hard-coding a different relative-path expression.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = PLUGIN_ROOT / "templates" / "ai_prompts"

__all__ = ["PLUGIN_ROOT", "TEMPLATE_DIR"]
