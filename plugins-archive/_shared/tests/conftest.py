"""Pytest bootstrap for archived ``_shared`` tests.

Adds ``plugins-archive/`` to ``sys.path`` so test modules can do
``from _shared import X``.  These tests are run on demand only — they
are excluded from the main project CI (see project README).
"""

from __future__ import annotations

import sys
from pathlib import Path

_archive_root = Path(__file__).resolve().parents[2]
if str(_archive_root) not in sys.path:
    sys.path.insert(0, str(_archive_root))
