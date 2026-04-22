"""Tests for openakita.plugins.compat — plugin version compatibility checking.

Critical regression coverage for the v1↔v2 plugin_api compatibility window
that was introduced when the host bumped to PLUGIN_API_VERSION = "2.0.0".

Without this window, every ~2 plugin authored before the bump would
silently fail to load (errors set, ok=False) and the entire plugin
ecosystem would orphan. See plan plugin_overhaul_standard_playbook.
"""

from __future__ import annotations

import pytest

from openakita.plugins.compat import (
    PLUGIN_API_COMPAT_WINDOW,
    PLUGIN_API_VERSION,
    PLUGIN_UI_API_VERSION,
    check_compatibility,
)
from openakita.plugins.manifest import PluginManifest


def _manifest(plugin_id: str, **requires: str) -> PluginManifest:
    """Build a minimal in-memory manifest for compat testing.

    We bypass parse_manifest because that requires plugin.json on disk
    and exercises orthogonal validation paths.
    """
    return PluginManifest(
        id=plugin_id,
        name=plugin_id,
        version="1.0.0",
        type="python",
        entry="plugin.py",
        permissions=["tools.register"],
        requires=requires,
    )


class TestPluginApiCompatibility:
    """plugin_api ~N spec must be checked against PLUGIN_API_VERSION."""

    def test_host_version_advertises_v2(self) -> None:
        assert PLUGIN_API_VERSION.startswith("2."), (
            "Host plugin_api must be v2 family — Phase 0 of the overhaul "
            "playbook depends on this. Update the playbook if you intend "
            "to bump again."
        )

    def test_compat_window_includes_v1_and_v2(self) -> None:
        assert 1 in PLUGIN_API_COMPAT_WINDOW
        assert 2 in PLUGIN_API_COMPAT_WINDOW

    def test_v2_plugin_loads_cleanly(self) -> None:
        m = _manifest("p", plugin_api="~2")
        result = check_compatibility(m)
        assert result.ok is True
        assert result.errors == []
        assert result.warnings == []

    def test_v1_plugin_loads_with_warning(self) -> None:
        """v1 plugins remain loadable during the compatibility window."""
        m = _manifest("legacy-p", plugin_api="~1")
        result = check_compatibility(m)
        assert result.ok is True, (
            "v1 plugins MUST load — they form the existing ecosystem. "
            f"Got errors: {result.errors}"
        )
        assert any(
            "compatibility window" in w for w in result.warnings
        ), f"Expected migration warning, got {result.warnings}"

    def test_future_v3_plugin_rejected(self) -> None:
        """~3 is outside the window — must be rejected to surface real
        breaking changes when the next major lands."""
        m = _manifest("future-p", plugin_api="~3")
        result = check_compatibility(m)
        assert result.ok is False
        assert any("compatibility window" in e for e in result.errors)

    def test_explicit_lower_bound_satisfied(self) -> None:
        m = _manifest("p", plugin_api=">=1.0.0")
        result = check_compatibility(m)
        assert result.ok is True

    def test_explicit_lower_bound_unsatisfied(self) -> None:
        m = _manifest("p", plugin_api=">=99.0.0")
        result = check_compatibility(m)
        assert result.ok is False

    def test_minor_version_warning_within_same_major(self) -> None:
        """~2.5 against current 2.0 should warn but still load."""
        m = _manifest("p", plugin_api="~2.5")
        result = check_compatibility(m)
        assert result.ok is True
        assert any("some features may be missing" in w for w in result.warnings)

    def test_unparseable_spec_warns_only(self) -> None:
        m = _manifest("p", plugin_api="~not-a-version")
        result = check_compatibility(m)
        assert result.ok is True
        assert any("Cannot parse" in w for w in result.warnings)

    def test_empty_spec_is_noop(self) -> None:
        m = _manifest("p", plugin_api="")
        result = check_compatibility(m)
        assert result.ok is True
        assert result.errors == []
        assert result.warnings == []


class TestPluginUiApiUnchanged:
    """plugin_ui_api should still target v1 — UI bundles unaffected by Phase 0."""

    def test_ui_api_still_v1(self) -> None:
        assert PLUGIN_UI_API_VERSION.startswith("1."), (
            "UI API surface is unchanged in Phase 0 — bumping it would "
            "force every plugin UI to retest. If you intend to bump, "
            "open a separate plan."
        )


@pytest.mark.parametrize(
    "spec, expect_ok",
    [
        ("~1", True),
        ("~2", True),
        ("~3", False),
        ("~10", False),
        (">=1.0.0", True),
        (">=2.0.0", True),
        (">=99.0.0", False),
    ],
)
def test_plugin_api_matrix(spec: str, expect_ok: bool) -> None:
    m = _manifest("p", plugin_api=spec)
    result = check_compatibility(m)
    assert result.ok is expect_ok, (
        f"spec={spec!r} expected ok={expect_ok}, got {result}"
    )
