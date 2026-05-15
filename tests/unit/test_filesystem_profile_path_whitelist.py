"""Stage 1 — Profile-aware path whitelist in :class:`FilesystemHandler`.

Locks the invariant that ``FilesystemHandler._allowed_roots()`` short-
circuits to ``[]`` (= "no whitelist check") for both ``off`` and ``trust``
profiles, and otherwise honours ``cfg.workspace.paths``.

Why this lives in its own file:

- The behaviour is a small but security-relevant contract spanning
  ``policy_v2.profile`` ↔ ``filesystem._allowed_roots``. Pinning it in a
  dedicated file makes the invariant grep-able and immune to accidental
  refactor drift.
- The companion E2E behaviour (``_guard_path_boundary`` returning ``None``
  when ``_allowed_roots()`` is empty) is also asserted, so future changes
  to either side trip the test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.tools.handlers.filesystem import FilesystemHandler


def _make_cfg(
    *,
    enabled: bool = True,
    profile_current: str = "protect",
    workspace_paths: list[str] | None = None,
) -> Any:
    """Build a minimal duck-typed cfg compatible with ``_allowed_roots``."""
    return SimpleNamespace(
        enabled=enabled,
        profile=SimpleNamespace(current=profile_current),
        workspace=SimpleNamespace(paths=list(workspace_paths or [])),
    )


def _make_handler(tmp_path) -> FilesystemHandler:
    agent = MagicMock()
    agent.default_cwd = str(tmp_path)
    return FilesystemHandler(agent)


# ---------------------------------------------------------------------------
# Short-circuit cases — _allowed_roots() returns []
# ---------------------------------------------------------------------------


class TestAllowedRootsShortCircuits:
    """Profiles that bypass the workspace path whitelist."""

    def test_profile_off_returns_empty(self, tmp_path, monkeypatch):
        """``off`` profile → empty roots (whitelist disabled)."""
        cfg = _make_cfg(profile_current="off", workspace_paths=[str(tmp_path)])
        monkeypatch.setattr("openakita.core.policy_v2.get_config_v2", lambda: cfg)

        handler = _make_handler(tmp_path)
        assert handler._allowed_roots() == []

    def test_profile_trust_returns_empty_stage1_invariant(self, tmp_path, monkeypatch):
        """**Stage 1 invariant**: ``trust`` profile → empty roots (no whitelist).

        Before Stage 1, ``trust`` fell through to the protect branch and
        applied ``cfg.workspace.paths``, contradicting its documented
        "trust the LLM to pick any path" semantics. This test locks the
        post-Stage-1 behaviour so a future revert is caught immediately.
        """
        cfg = _make_cfg(profile_current="trust", workspace_paths=[str(tmp_path)])
        monkeypatch.setattr("openakita.core.policy_v2.get_config_v2", lambda: cfg)

        handler = _make_handler(tmp_path)
        assert handler._allowed_roots() == []

    def test_security_globally_disabled_returns_empty(self, tmp_path, monkeypatch):
        """``cfg.enabled = False`` → empty roots regardless of profile."""
        cfg = _make_cfg(
            enabled=False,
            profile_current="protect",
            workspace_paths=[str(tmp_path)],
        )
        monkeypatch.setattr("openakita.core.policy_v2.get_config_v2", lambda: cfg)

        handler = _make_handler(tmp_path)
        assert handler._allowed_roots() == []


# ---------------------------------------------------------------------------
# Whitelist-active cases — workspace.paths is honoured
# ---------------------------------------------------------------------------


class TestAllowedRootsWithWhitelist:
    """Profiles that read ``cfg.workspace.paths``."""

    @pytest.mark.parametrize("profile", ["protect", "strict", "custom"])
    def test_workspace_paths_returned_for_active_profiles(
        self, tmp_path, monkeypatch, profile
    ):
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        cfg = _make_cfg(profile_current=profile, workspace_paths=[str(ws_dir)])
        monkeypatch.setattr("openakita.core.policy_v2.get_config_v2", lambda: cfg)

        handler = _make_handler(tmp_path)
        roots = handler._allowed_roots()

        assert str(ws_dir) in roots, (
            f"profile={profile!r}: expected workspace path {ws_dir} in roots {roots}"
        )

    def test_internal_data_dir_always_appended(self, tmp_path, monkeypatch):
        """``settings.data_dir`` 永远附加，避免内部读写被白名单卡住。"""
        from openakita.config import settings

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        cfg = _make_cfg(profile_current="protect", workspace_paths=[str(ws_dir)])
        monkeypatch.setattr("openakita.core.policy_v2.get_config_v2", lambda: cfg)

        handler = _make_handler(tmp_path)
        roots = handler._allowed_roots()

        assert str(settings.data_dir) in roots


# ---------------------------------------------------------------------------
# E2E: _guard_path_boundary respects the short-circuit
# ---------------------------------------------------------------------------


class TestGuardPathBoundary:
    """``_guard_path_boundary`` must return ``None`` (= allow) when roots empty."""

    def test_trust_profile_allows_any_absolute_path(self, tmp_path, monkeypatch):
        """End-to-end: trust profile lets a path **outside** any whitelist through."""
        cfg = _make_cfg(profile_current="trust", workspace_paths=[str(tmp_path)])
        monkeypatch.setattr("openakita.core.policy_v2.get_config_v2", lambda: cfg)

        handler = _make_handler(tmp_path)
        # Far-away path that would normally violate the protect-mode whitelist.
        far_away = "C:/some/totally/unrelated/path" if tmp_path.drive else "/tmp/elsewhere"
        result = handler._guard_path_boundary(far_away, op="read")
        assert result is None, f"trust profile should allow any path, got: {result!r}"

    def test_protect_profile_blocks_outside_path(self, tmp_path, monkeypatch):
        """E2E regression: protect profile must still block out-of-whitelist paths."""
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        cfg = _make_cfg(profile_current="protect", workspace_paths=[str(ws_dir)])
        monkeypatch.setattr("openakita.core.policy_v2.get_config_v2", lambda: cfg)

        handler = _make_handler(tmp_path)
        # Path outside ws_dir AND outside settings.data_dir.
        outside = str(tmp_path / "outside" / "secret.txt")
        result = handler._guard_path_boundary(outside, op="read")
        assert result is not None, (
            "protect profile must reject paths outside the workspace whitelist; "
            f"got None (allowed) for {outside!r}"
        )
        assert "路径名单拒绝" in result
