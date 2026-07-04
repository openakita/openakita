"""Smoke test: happyhorse-video declares a valid v2 WORKBENCH manifest.

Adds a load-time guard so any future edit to the plugin's
``WORKBENCH`` constant that would prevent the v2 ``WorkbenchNode``
from instantiating is caught by CI rather than at runtime when the
aigc-video-studio template tries to bind to a workbench mode.

This test sits inside the plugin's own test folder (next to
``test_happyhorse_workbench_protocol.py``) because the manifest is
the plugin's contract with the runtime; co-locating the guard with
the plugin keeps it discoverable and lets plugin authors run the
check without remembering to invoke a runtime test path.
"""

from __future__ import annotations

import pytest
from _plugin_loader import load_happyhorse_plugin

from openakita.runtime.nodes import WorkbenchManifest, WorkbenchManifestError


@pytest.fixture(scope="module")
def workbench() -> dict:
    plugin = load_happyhorse_plugin()
    raw = getattr(plugin, "WORKBENCH", None)
    assert raw is not None, "plugin.py must export a top-level WORKBENCH dict"
    return raw


def test_workbench_constant_parses_cleanly(workbench: dict) -> None:
    manifest = WorkbenchManifest.parse(workbench)
    assert manifest.id == "happyhorse-video"
    assert manifest.version >= 2
    assert manifest.default_mode == "art_director"
    assert manifest.title.startswith("Happy Horse")


def test_workbench_modes_match_user_facing_roles(workbench: dict) -> None:
    manifest = WorkbenchManifest.parse(workbench)
    mode_ids = [m.id for m in manifest.modes]
    expected = {"art_director", "image_artist", "video_animator", "portrait_actor"}
    assert set(mode_ids) == expected, (
        "happyhorse-video manifest must declare exactly these four roles "
        "the aigc-video-studio template binds to"
    )


def test_each_mode_lists_its_canonical_tool_subset(workbench: dict) -> None:
    """Each mode must include the unique workhorse tool that defines it.

    Catches the regression where a refactor accidentally drops the tool
    that gives a mode its identity (e.g. removing ``hh_storyboard_decompose``
    from ``art_director`` would silently degrade the long-video pipeline).
    """
    manifest = WorkbenchManifest.parse(workbench)
    canonical = {
        "art_director": {"hh_storyboard_decompose", "hh_long_video_create"},
        "image_artist": {"hh_image_create", "hh_image_edit"},
        "video_animator": {"hh_t2v", "hh_i2v"},
        "portrait_actor": {"hh_photo_speak", "hh_pose_drive"},
    }
    for mode_id, must_have in canonical.items():
        mode = manifest.mode(mode_id)
        missing = must_have - set(mode.tools)
        assert not missing, f"mode {mode_id!r} is missing canonical tools: {missing}"


def test_no_mode_grants_arbitrary_unscoped_powers(workbench: dict) -> None:
    """Defence against a copy-paste bug that re-exports every tool to every mode.

    The whole point of mode-scoped allow-lists is to prevent the
    "give the agent everything" pattern. Ensure no mode is granted
    *all* canonical tools across the studio.
    """
    manifest = WorkbenchManifest.parse(workbench)
    every_canonical = {
        "hh_storyboard_decompose",
        "hh_long_video_create",
        "hh_image_create",
        "hh_t2v",
        "hh_photo_speak",
    }
    for mode in manifest.modes:
        leak = every_canonical & set(mode.tools)
        assert leak != every_canonical, (
            f"mode {mode.id!r} grants the full canonical tool set; "
            "mode allow-lists are meant to scope, not whitelist everything"
        )


def test_invalid_mutation_raises_manifest_error() -> None:
    """If someone deletes a required field, parse must fail loudly."""
    raw = {"id": "happyhorse-video", "title": "x"}  # no modes
    with pytest.raises(WorkbenchManifestError):
        WorkbenchManifest.parse(raw)
