"""pytest helpers for the excel-maker plugin."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = REPO_ROOT / "src"

for _path in (str(SRC_ROOT), str(PLUGIN_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


@pytest.fixture(autouse=True)
def isolate_plugin_imports() -> None:
    sys.modules.pop("plugin", None)
    yield
    sys.modules.pop("plugin", None)
    sys.modules.pop("openakita.plugins.api", None)

