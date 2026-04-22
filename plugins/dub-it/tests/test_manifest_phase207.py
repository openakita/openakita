"""Phase 2-07 manifest sanity tests for dub-it.

dub-it intentionally stays as a teaching/scaffolding example for the
``extract → transcribe → translate → TTS → mux`` pipeline pattern; it
does NOT pull in real ASR/TTS providers (use ``video-translator`` for
that). These tests just lock in the manifest contract so the plugin
keeps loading correctly under the post-Phase-0 host:

* SDK requirement matches the post-overhaul minimum (>=0.6.0).
* No legacy ``depends`` field on sibling plugins (it never had one,
  but we assert the absence so future regressions stay obvious).
* Top-level ``requires.plugin_api`` is the v2 host contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


@pytest.fixture()
def manifest() -> dict:
    return json.loads((_HERE / "plugin.json").read_text(encoding="utf-8"))


def test_sdk_requirement_is_overhaul_minimum(manifest: dict) -> None:
    assert manifest["requires"]["sdk"] == ">=0.6.0,<1.0.0"


def test_plugin_api_targets_v2(manifest: dict) -> None:
    assert manifest["requires"]["plugin_api"] == "~2"


def test_no_sibling_depends(manifest: dict) -> None:
    """dub-it must NOT depend on sibling plugins. Production end-to-end
    flows live in ``video-translator``; dub-it is the scaffolding."""
    assert "depends" not in manifest


def test_engine_does_not_use_load_sibling() -> None:
    """Belt-and-braces: confirm the engine module is self-contained."""
    src = (_HERE / "dub_engine.py").read_text(encoding="utf-8")
    assert "_load_sibling" not in src
