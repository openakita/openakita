"""Tests for runtime.nodes.manifest — WorkbenchManifest validation."""

from __future__ import annotations

import pytest

from openakita.runtime.nodes import (
    WorkbenchManifest,
    WorkbenchManifestError,
    WorkbenchMode,
)


def _good_minimal() -> dict:
    return {
        "id": "happyhorse-video",
        "title": "Happy Horse Video Studio",
        "modes": [
            {
                "id": "image_artist",
                "label": "Image Artist",
                "tools": ["hh_t2i", "hh_i2i"],
            }
        ],
    }


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_minimal_manifest_parses() -> None:
    m = WorkbenchManifest.parse(_good_minimal())
    assert m.id == "happyhorse-video"
    assert m.title == "Happy Horse Video Studio"
    assert m.version == 1
    assert m.default_mode == "image_artist"
    assert len(m.modes) == 1
    assert m.modes[0].tools == ("hh_t2i", "hh_i2i")


def test_full_manifest_parses_and_round_trips_lookup() -> None:
    raw = {
        "id": "happyhorse-video",
        "title": "Happy Horse Video Studio",
        "description": "AIGC studio",
        "version": 2,
        "ui": {"url": "/p/x.html", "min_width": 720, "icon": "/p/icon.svg"},
        "capabilities": ["t2i", "i2v"],
        "modes": [
            {
                "id": "art_director",
                "label": "Art Director",
                "tools": ["hh_storyboard_decompose", "org_delegate_task"],
                "system_prompt_override": "You are the Art Director.",
                "guardrails": [{"type": "min_items", "field": "shots", "n": 8}],
                "ui_panel": "director",
            },
            {
                "id": "image_artist",
                "label": "Image Artist",
                "tools": ["hh_t2i", "hh_i2i"],
                "ui_panel": "imagery",
            },
        ],
        "default_mode": "art_director",
    }
    m = WorkbenchManifest.parse(raw)
    assert m.version == 2
    assert m.ui.url == "/p/x.html"
    assert m.ui.min_width == 720
    assert m.capabilities == ("t2i", "i2v")
    director = m.mode("art_director")
    assert isinstance(director, WorkbenchMode)
    assert director.system_prompt_override == "You are the Art Director."
    assert director.guardrails == ({"type": "min_items", "field": "shots", "n": 8},)
    assert director.ui_panel == "director"


# ---------------------------------------------------------------------------
# rejection paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d.pop("id"), "id"),
        (lambda d: d.update(id=""), "id"),
        (lambda d: d.update(version=0), "version"),
        (lambda d: d.update(modes=[]), "modes"),
        (
            lambda d: d.update(
                modes=[{"id": "a", "tools": ["x"]}, {"id": "a", "tools": ["y"]}]
            ),
            "duplicate mode id",
        ),
        (
            lambda d: d.update(
                modes=[{"id": "a", "tools": []}],
            ),
            "tools",
        ),
        (
            lambda d: d.update(
                modes=[{"id": "a", "tools": ["x"]}],
                default_mode="bogus",
            ),
            "default_mode",
        ),
        (lambda d: d.update(capabilities="oops"), "capabilities"),
        (lambda d: d.update(ui="oops"), "ui"),
        (lambda d: d.update(ui={"url": 5}), "ui.url"),
    ],
)
def test_invalid_manifests_raise(mutate, expected: str) -> None:
    raw = _good_minimal()
    mutate(raw)
    with pytest.raises(WorkbenchManifestError, match=expected):
        WorkbenchManifest.parse(raw)


def test_mode_lookup_for_unknown_id_raises_keyerror() -> None:
    m = WorkbenchManifest.parse(_good_minimal())
    with pytest.raises(KeyError):
        m.mode("does-not-exist")


def test_default_mode_falls_back_to_first_mode_when_omitted() -> None:
    raw = _good_minimal()
    raw["modes"] = [
        {"id": "first", "tools": ["a"]},
        {"id": "second", "tools": ["b"]},
    ]
    raw.pop("default_mode", None)
    m = WorkbenchManifest.parse(raw)
    assert m.default_mode == "first"
