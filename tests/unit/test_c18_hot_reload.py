"""C18 Phase A — POLICIES.yaml hot-reload tests.

Coverage:
1. ``HotReloadConfig`` schema validation (range / type).
2. ``PolicyHotReloader`` change detection: mtime + content hash dedup.
3. Reload success path: valid YAML edit → ``rebuild_engine_v2`` called +
   audit row written + ``on_reload(True, ...)``.
4. Reload failure path: invalid YAML edit → engine NOT swapped (LKG kept)
   + audit row with ``ok=False`` + ``on_reload(False, ...)``.
5. Content-unchanged mtime touch → NO rebuild call.
6. Singleton start/stop idempotent.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from openakita.core.policy_v2 import audit_chain, global_engine, hot_reload
from openakita.core.policy_v2.hot_reload import (
    PolicyHotReloader,
    get_hot_reloader,
    start_hot_reloader,
    stop_hot_reloader,
)
from openakita.core.policy_v2.schema import HotReloadConfig

# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestHotReloadConfig:
    def test_defaults_match_documentation(self) -> None:
        cfg = HotReloadConfig()
        assert cfg.enabled is False, "C18 Phase A ships disabled by default"
        assert cfg.poll_interval_seconds == 5.0
        assert cfg.debounce_seconds == 0.5

    def test_poll_interval_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            HotReloadConfig(poll_interval_seconds=0.1)

    def test_poll_interval_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            HotReloadConfig(poll_interval_seconds=10000)

    def test_debounce_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            HotReloadConfig(debounce_seconds=-0.5)

    def test_debounce_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            HotReloadConfig(debounce_seconds=120)

    def test_strict_extra_fields_forbidden(self) -> None:
        """typo protection: ``poll_seconds`` instead of
        ``poll_interval_seconds`` must error, not silently default."""
        with pytest.raises(ValidationError):
            HotReloadConfig(poll_seconds=5)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Reloader unit tests (no real polling — invoke _check_once directly so
# tests stay deterministic and ~ms fast)
# ---------------------------------------------------------------------------


_VALID_YAML = """\
version: 2
security:
  workspace:
    paths: ["${CWD}"]
"""

# Top-level ``policy_v2`` schema lives under ``security:``; the loader does
# ``raw.get("security") or {}`` before validation. So to make a YAML that
# actually fails strict validation we must put the bad fields under
# ``security:`` and target a strict typed field. ``confirmation.mode`` is
# an enum string — passing a list there triggers ValidationError on the
# strict pydantic model.
_INVALID_YAML = """\
version: 2
security:
  confirmation:
    mode: 12345  # must be a string enum value, not an int
  unknown_section_strict_extra_forbids: yes
"""

_DIFFERENT_VALID_YAML = """\
version: 2
security:
  workspace:
    paths: ["${CWD}", "/tmp"]
"""


@pytest.fixture
def policies_path(tmp_path: Path) -> Path:
    path = tmp_path / "POLICIES.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_engine_state() -> None:
    """Each test gets a fresh engine + LKG + reloader singleton."""
    stop_hot_reloader(timeout=0.5)
    global_engine.reset_engine_v2(clear_explicit_lookup=True)
    global_engine._clear_last_known_good()
    audit_chain.reset_writers_for_testing()
    yield
    stop_hot_reloader(timeout=0.5)
    global_engine.reset_engine_v2(clear_explicit_lookup=True)
    global_engine._clear_last_known_good()
    audit_chain.reset_writers_for_testing()


def _make_reloader(
    path: Path,
    events: list[tuple[bool, str]],
    *,
    debounce: float = 0.0,
) -> PolicyHotReloader:
    def _on(ok: bool, reason: str) -> None:
        events.append((ok, reason))

    return PolicyHotReloader(
        path,
        poll_interval_seconds=1.0,
        debounce_seconds=debounce,
        on_reload=_on,
    )


def _touch_path_with_new_mtime(path: Path, *, bytes_: bytes | None = None) -> None:
    """Force a mtime bump even on filesystems with low timestamp resolution.

    Some CI filesystems have 1-second mtime granularity; bumping the
    clock by 2 seconds guarantees ``stat().st_mtime`` advances.
    """
    if bytes_ is not None:
        path.write_bytes(bytes_)
    new_mtime = time.time() + 2.0
    os.utime(path, (new_mtime, new_mtime))


class TestChangeDetection:
    def test_no_change_no_callback(self, policies_path: Path) -> None:
        events: list[tuple[bool, str]] = []
        reloader = _make_reloader(policies_path, events)
        reloader._check_once()  # mtime same as init → no-op
        assert events == []

    def test_mtime_change_with_same_content_skips_rebuild(
        self,
        policies_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``touch POLICIES.yaml`` shouldn't trigger rebuild."""
        rebuild_calls: list[Any] = []

        def _fake_rebuild(**kwargs):
            rebuild_calls.append(kwargs)

        monkeypatch.setattr(global_engine, "rebuild_engine_v2", _fake_rebuild)
        events: list[tuple[bool, str]] = []
        reloader = _make_reloader(policies_path, events)

        # Touch the file (mtime advances, content unchanged).
        _touch_path_with_new_mtime(policies_path)
        reloader._check_once()

        assert rebuild_calls == [], "rebuild should not run on content-equal touch"
        assert events == [(False, "content unchanged")]

    def test_content_change_triggers_rebuild(
        self,
        policies_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rebuild_calls: list[Any] = []

        class _FakeEngine:
            pass

        fake_engine = _FakeEngine()

        def _fake_rebuild(**kwargs):
            rebuild_calls.append(kwargs)
            return fake_engine

        # We also need _get_last_known_good to advance after rebuild —
        # easiest: monkeypatch it to return distinct objects per call.
        lkg_values = [object(), object()]  # before, after

        def _fake_lkg():
            return lkg_values.pop(0) if lkg_values else None

        monkeypatch.setattr(global_engine, "rebuild_engine_v2", _fake_rebuild)
        monkeypatch.setattr(global_engine, "_get_last_known_good", _fake_lkg)

        events: list[tuple[bool, str]] = []
        reloader = _make_reloader(policies_path, events)

        _touch_path_with_new_mtime(policies_path, bytes_=_DIFFERENT_VALID_YAML.encode())
        reloader._check_once()

        assert len(rebuild_calls) == 1
        assert rebuild_calls[0]["yaml_path"] == policies_path
        assert events == [(True, "engine rebuilt")]


class TestReloadOutcomes:
    def test_valid_yaml_real_reload_swaps_engine(
        self, policies_path: Path
    ) -> None:
        """End-to-end with the real ``rebuild_engine_v2``."""
        events: list[tuple[bool, str]] = []
        reloader = _make_reloader(policies_path, events)

        # Establish a baseline engine first (forces LKG init).
        old_engine = global_engine.rebuild_engine_v2(yaml_path=policies_path)
        assert global_engine._get_last_known_good() is not None

        _touch_path_with_new_mtime(
            policies_path, bytes_=_DIFFERENT_VALID_YAML.encode()
        )
        reloader._check_once()

        new_engine = global_engine.get_engine_v2()
        assert new_engine is not old_engine
        assert events == [(True, "engine rebuilt")]

    def test_invalid_yaml_keeps_lkg_and_logs_failure(
        self, policies_path: Path
    ) -> None:
        """When new YAML fails validation, ``rebuild_engine_v2`` still
        builds a fresh engine object — but from the *same* LKG config.
        So the contract we promise the user is:

        * config identity is preserved (LKG kept)
        * security-critical settings (safety_immune paths etc.) unchanged
        * audit + callback report ``ok=False`` with the validation reason
        """
        events: list[tuple[bool, str]] = []
        reloader = _make_reloader(policies_path, events)

        # Baseline.
        global_engine.rebuild_engine_v2(yaml_path=policies_path)
        good_lkg = global_engine._get_last_known_good()
        assert good_lkg is not None
        good_config = global_engine.get_config_v2()

        # Now corrupt the file.
        _touch_path_with_new_mtime(policies_path, bytes_=_INVALID_YAML.encode())
        reloader._check_once()

        # Config identity must NOT advance (LKG kept us safe). Engine
        # object identity may change because rebuild_engine_v2 always
        # constructs a fresh PolicyEngineV2 wrapper — but it's wrapping
        # the *same* validated config.
        post_lkg = global_engine._get_last_known_good()
        assert post_lkg is good_lkg, "LKG must not change on failed validation"
        post_config = global_engine.get_config_v2()
        assert post_config is good_config, (
            "config identity must be preserved across failed reload"
        )

        assert len(events) == 1
        ok, reason = events[0]
        assert ok is False
        assert "validation failed" in reason or "rebuild raised" in reason


class TestAuditEmission:
    def test_audit_row_written_on_success(
        self, policies_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The hot-reload module must emit an audit row through
        ``get_audit_logger`` so verify_chain catches tampering with the
        reload history."""
        # Point audit logger at a temp file.
        from openakita.core import audit_logger as al

        audit_path = tmp_path / "audit.jsonl"
        fake_logger = al.AuditLogger(
            path=str(audit_path), enabled=True, include_chain=False
        )
        monkeypatch.setattr(al, "get_audit_logger", lambda: fake_logger)

        # Baseline + reload.
        events: list[tuple[bool, str]] = []
        reloader = _make_reloader(policies_path, events)
        global_engine.rebuild_engine_v2(yaml_path=policies_path)
        _touch_path_with_new_mtime(
            policies_path, bytes_=_DIFFERENT_VALID_YAML.encode()
        )
        reloader._check_once()

        assert audit_path.exists()
        text = audit_path.read_text(encoding="utf-8").strip()
        assert text, "audit file must contain at least one reload row"
        # The last line should be our reload event.
        last = text.splitlines()[-1]
        import json

        row = json.loads(last)
        assert row["tool"] == "<policy_hot_reload>"
        assert row["decision"] in {"reload_ok", "reload_failed"}
        assert row["policy"] == "policy_hot_reload"


class TestFileMissing:
    def test_disappearing_file_does_not_crash(self, policies_path: Path) -> None:
        events: list[tuple[bool, str]] = []
        reloader = _make_reloader(policies_path, events)

        policies_path.unlink()
        # Must not raise.
        reloader._check_once()
        # No event fired (no reload attempt — just metadata observation).
        assert events == []


class TestSingletonAPI:
    def test_start_returns_none_when_disabled(
        self, policies_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If POLICIES.yaml says ``hot_reload.enabled=false`` → no-op."""
        monkeypatch.setattr(
            hot_reload,
            "_safe_get_hot_reload_cfg",
            lambda: HotReloadConfig(enabled=False),
        )
        result = start_hot_reloader(yaml_path=policies_path)
        assert result is None
        assert get_hot_reloader() is None

    def test_start_with_force_bypasses_config(
        self, policies_path: Path
    ) -> None:
        try:
            r = start_hot_reloader(
                yaml_path=policies_path,
                poll_interval_seconds=1.0,
                debounce_seconds=0.0,
                force=True,
            )
            assert r is not None
            assert r.is_running()
            assert get_hot_reloader() is r
        finally:
            stop_hot_reloader(timeout=1.0)

    def test_start_idempotent(self, policies_path: Path) -> None:
        try:
            r1 = start_hot_reloader(
                yaml_path=policies_path,
                poll_interval_seconds=1.0,
                debounce_seconds=0.0,
                force=True,
            )
            r2 = start_hot_reloader(
                yaml_path=policies_path,
                poll_interval_seconds=10.0,
                force=True,
            )
            assert r1 is r2, "second start must return the same instance"
            assert r1.poll_interval == 1.0, "params from first call retained"
        finally:
            stop_hot_reloader(timeout=1.0)

    def test_stop_idempotent(self) -> None:
        # Should never raise even when no reloader is running.
        stop_hot_reloader(timeout=0.1)
        stop_hot_reloader(timeout=0.1)

    def test_start_returns_none_when_yaml_missing(
        self, tmp_path: Path
    ) -> None:
        result = start_hot_reloader(
            yaml_path=tmp_path / "does-not-exist.yaml", force=True
        )
        assert result is None


class TestThreadLifecycle:
    def test_thread_actually_polls_and_exits_cleanly(
        self, policies_path: Path
    ) -> None:
        """Smoke test: real thread, short interval, content change is
        picked up within ~poll_interval."""
        observed: list[tuple[bool, str]] = []
        reloader = PolicyHotReloader(
            policies_path,
            poll_interval_seconds=0.1,
            debounce_seconds=0.0,
            on_reload=lambda ok, reason: observed.append((ok, reason)),
        )
        try:
            # Establish baseline LKG so subsequent reload counts as advance.
            global_engine.rebuild_engine_v2(yaml_path=policies_path)
            reloader.start()
            assert reloader.is_running()

            _touch_path_with_new_mtime(
                policies_path, bytes_=_DIFFERENT_VALID_YAML.encode()
            )
            # Wait up to 2s for at least one event.
            deadline = time.monotonic() + 2.0
            while not observed and time.monotonic() < deadline:
                time.sleep(0.05)
            assert observed, "watcher thread didn't observe the change within 2s"
        finally:
            reloader.stop(timeout=1.0)
        assert not reloader.is_running()
