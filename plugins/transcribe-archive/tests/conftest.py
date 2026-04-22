"""Test bootstrap for the transcribe-archive plugin.

Mirrors the seedance-video / bgm-suggester convention: insert the
plugin root into ``sys.path`` so tests can ``import transcribe_engine``
without hacking the package into ``setup.py``.

We keep this isolated from the host's pytest config so the plugin's
test suite can run in a clean venv (``py -3.11 -m pytest
plugins/transcribe-archive/tests``) without dragging the full repo
imports.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
