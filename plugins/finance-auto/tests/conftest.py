"""Shared pytest fixtures for finance-auto tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``finance_auto_backend`` importable when tests are run from the repo
# root via ``pytest plugins/finance-auto/tests``.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
