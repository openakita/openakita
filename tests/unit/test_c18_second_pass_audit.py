"""C18 二轮自审修复 —— bug-by-bug regression coverage.

After shipping Phase A..D + F we self-reviewed and found 4 real
issues. This file owns the regression tests so future refactors can't
silently re-break them.

1. **BUG-A1**: ``PolicyHotReloader._do_reload`` reported ``ok=True``
   for an invalid YAML reload when LKG was ``None`` at the time of the
   reload attempt — because the "LKG didn't change" check only fires
   for "LKG was set BEFORE and didn't advance". When LKG was never set
   (process started with broken YAML), an invalid edit silently looked
   like success.

2. **BUG-C1**: ``audit_logger._global_audit`` is a process-wide
   singleton; ``get_audit_logger()`` caches its first construction.
   Hot-reload or ``OPENAKITA_AUDIT_LOG_PATH`` could change
   ``audit.log_path`` in ``_config`` but subsequent audit writes still
   went to the original file. ``rebuild_engine_v2`` now resets the
   singleton when any audit field changes.

3. **BUG-C2** (latent deadlock): ``_audit_env_overrides`` was called
   from inside ``rebuild_engine_v2``'s ``_lock`` scope. When
   ``_global_audit is None`` (e.g. after ``reset_audit_logger`` from
   BUG-C1 fix), ``get_audit_logger()`` would re-enter
   ``get_config_v2()`` which also tries to acquire ``_lock``. Lock is
   ``threading.Lock`` (non-reentrant) → process deadlock. Fix: pass
   ``cfg`` directly so the function constructs an ephemeral
   AuditLogger without singleton/lock re-entry.

4. **IMPROVEMENT-B1** (frontend, not Python-testable here): the batch
   resolve callback now checks ``r.ok`` + JSON ``status`` before
   clearing the local queue. Covered by manual smoke test +
   ``scripts/c18_audit.py`` checking the conditional is present in
   ChatView.tsx.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.core import audit_logger as al
from openakita.core.policy_v2 import audit_chain, global_engine, hot_reload
from openakita.core.policy_v2.hot_reload import PolicyHotReloader

_VALID_YAML = "version: 2\nsecurity:\n  workspace:\n    paths: ['${CWD}']\n"
_INVALID_YAML = (
    "version: 2\n"
    "security:\n"
    "  confirmation:\n"
    "    mode: 12345\n"  # mode must be a string enum, int triggers ValidationError
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Each test gets a clean engine + LKG + audit logger."""
    hot_reload.stop_hot_reloader(timeout=0.5)
    global_engine.reset_engine_v2(clear_explicit_lookup=True)
    global_engine._clear_last_known_good()
    al.reset_audit_logger()
    audit_chain.reset_writers_for_testing()
    yield
    hot_reload.stop_hot_reloader(timeout=0.5)
    global_engine.reset_engine_v2(clear_explicit_lookup=True)
    global_engine._clear_last_known_good()
    al.reset_audit_logger()
    audit_chain.reset_writers_for_testing()


# ---------------------------------------------------------------------------
# BUG-A1: invalid reload with LKG=None must report ok=False
# ---------------------------------------------------------------------------


class TestBugA1HotReloadFailureWhenLkgNone:
    def test_invalid_reload_with_no_lkg_reports_failure(
        self, tmp_path: Path
    ) -> None:
        """Process starts with broken YAML → LKG stays None → operator
        tries to edit it (with another typo) → reload must be marked
        failed, not silently called 'engine rebuilt'."""
        policies = tmp_path / "POLICIES.yaml"
        # Initially invalid: rebuild_engine_v2 falls back to defaults,
        # LKG stays None.
        policies.write_text(_INVALID_YAML, encoding="utf-8")
        global_engine.rebuild_engine_v2(yaml_path=policies)
        assert global_engine._get_last_known_good() is None, (
            "broken initial YAML must leave LKG=None — test precondition"
        )

        events: list[tuple[bool, str]] = []
        reloader = PolicyHotReloader(
            policies,
            poll_interval_seconds=1.0,
            debounce_seconds=0.0,
            on_reload=lambda ok, reason: events.append((ok, reason)),
        )

        # Edit the file but keep it invalid (different typo). The new
        # mtime + hash trigger _do_reload, which calls rebuild_engine_v2,
        # which again hits _recover_from_load_failure → LKG stays None.
        import os
        import time

        new_invalid = _INVALID_YAML.replace("12345", "67890")
        policies.write_bytes(new_invalid.encode())
        new_mtime = time.time() + 2.0
        os.utime(policies, (new_mtime, new_mtime))

        reloader._check_once()

        assert len(events) == 1, "exactly one reload event expected"
        ok, reason = events[0]
        assert ok is False, (
            "BUG-A1 regression: invalid reload with LKG=None must report ok=False"
        )
        assert "no last-known-good" in reason or "validation" in reason

    def test_valid_first_promotion_still_reports_success(
        self, tmp_path: Path
    ) -> None:
        """Sanity: when LKG was None and the edit is VALID, the first
        successful reload must still report ok=True (LKG gets promoted
        for the first time)."""
        policies = tmp_path / "POLICIES.yaml"
        policies.write_text(_INVALID_YAML, encoding="utf-8")
        global_engine.rebuild_engine_v2(yaml_path=policies)
        assert global_engine._get_last_known_good() is None

        events: list[tuple[bool, str]] = []
        reloader = PolicyHotReloader(
            policies,
            poll_interval_seconds=1.0,
            debounce_seconds=0.0,
            on_reload=lambda ok, reason: events.append((ok, reason)),
        )

        import os
        import time

        policies.write_bytes(_VALID_YAML.encode())
        new_mtime = time.time() + 2.0
        os.utime(policies, (new_mtime, new_mtime))

        reloader._check_once()

        assert len(events) == 1
        ok, reason = events[0]
        assert ok is True, "first valid reload must promote LKG and report success"
        # LKG must now be populated.
        assert global_engine._get_last_known_good() is not None


# ---------------------------------------------------------------------------
# BUG-C1: audit_logger singleton must be invalidated when audit cfg changes
# ---------------------------------------------------------------------------


class TestBugC1AuditLoggerSingletonRefresh:
    def test_rebuild_with_changed_audit_path_resets_singleton(
        self, tmp_path: Path
    ) -> None:
        """A rebuild that flips ``audit.log_path`` must cause the next
        ``get_audit_logger()`` to construct a fresh logger pointing at
        the new path."""
        policies = tmp_path / "POLICIES.yaml"
        policies.write_text(
            "security:\n"
            "  audit:\n"
            f"    log_path: '{(tmp_path / 'first.jsonl').as_posix()}'\n",
            encoding="utf-8",
        )
        global_engine.rebuild_engine_v2(yaml_path=policies)
        logger_v1 = al.get_audit_logger()
        assert logger_v1._path == tmp_path / "first.jsonl"

        # Flip the YAML to a NEW audit path. After rebuild, the
        # singleton must be invalidated so the next get returns a
        # logger pointing at the new path.
        policies.write_text(
            "security:\n"
            "  audit:\n"
            f"    log_path: '{(tmp_path / 'second.jsonl').as_posix()}'\n",
            encoding="utf-8",
        )
        global_engine.rebuild_engine_v2(yaml_path=policies)
        logger_v2 = al.get_audit_logger()

        assert logger_v2 is not logger_v1, (
            "BUG-C1 regression: rebuild must invalidate the audit_logger "
            "singleton when audit.log_path changes"
        )
        assert logger_v2._path == tmp_path / "second.jsonl"

    def test_rebuild_with_unchanged_audit_keeps_singleton(
        self, tmp_path: Path
    ) -> None:
        """Don't churn the singleton when audit didn't actually change
        — keeps log file handle warm + avoids open-file storms on
        rapid hot-reload cycles."""
        policies = tmp_path / "POLICIES.yaml"
        policies.write_text(
            "security:\n"
            "  audit:\n"
            f"    log_path: '{(tmp_path / 'pinned.jsonl').as_posix()}'\n",
            encoding="utf-8",
        )
        global_engine.rebuild_engine_v2(yaml_path=policies)
        logger_v1 = al.get_audit_logger()

        # Rebuild without changing audit (touch another knob).
        policies.write_text(
            "security:\n"
            "  audit:\n"
            f"    log_path: '{(tmp_path / 'pinned.jsonl').as_posix()}'\n"
            "  confirmation:\n"
            "    timeout_seconds: 99\n",
            encoding="utf-8",
        )
        global_engine.rebuild_engine_v2(yaml_path=policies)
        logger_v2 = al.get_audit_logger()

        assert logger_v2 is logger_v1, (
            "rebuild must not reset audit_logger when audit cfg unchanged"
        )

    def test_env_override_audit_path_takes_effect_on_rebuild(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end: ``OPENAKITA_AUDIT_LOG_PATH`` set on a rebuild
        must result in subsequent audit writes landing in the new file,
        not the previous one."""
        # First rebuild without ENV: default audit path.
        policies = tmp_path / "POLICIES.yaml"
        policies.write_text("security: {}\n", encoding="utf-8")
        first_log = tmp_path / "first.jsonl"
        policies.write_text(
            "security:\n"
            "  audit:\n"
            f"    log_path: '{first_log.as_posix()}'\n",
            encoding="utf-8",
        )
        global_engine.rebuild_engine_v2(yaml_path=policies)
        # Force first write to confirm path.
        al.get_audit_logger().log(
            tool_name="probe", decision="allow", reason="warmup"
        )
        assert first_log.exists()
        rows_before = first_log.read_text(encoding="utf-8").splitlines()

        # Now flip via ENV and rebuild.
        second_log = tmp_path / "second.jsonl"
        monkeypatch.setenv("OPENAKITA_AUDIT_LOG_PATH", str(second_log))
        global_engine.rebuild_engine_v2(yaml_path=policies)
        al.get_audit_logger().log(
            tool_name="probe2", decision="allow", reason="after env"
        )

        assert second_log.exists(), "audit must land in ENV-overridden path"
        # Old file must not have grown beyond what we wrote earlier
        # (+ possibly the rebuild's own override audit row written under
        # the OLD logger — but in this test the override row is written
        # by _audit_env_overrides which fires BEFORE _config swap, so it
        # may land in first.jsonl. That's acceptable; the contract is
        # "subsequent tool decisions go to the new path", which we just
        # demonstrated via the probe2 row.)
        rows_after = first_log.read_text(encoding="utf-8").splitlines()
        # probe2 must NOT be in first.jsonl.
        for r in rows_after[len(rows_before):]:
            parsed = json.loads(r)
            assert parsed["tool"] != "probe2", (
                "BUG-C1 regression: writes after ENV change leaked into "
                "the previous audit file"
            )


# ---------------------------------------------------------------------------
# Sanity: no regressions in regular reload flow
# ---------------------------------------------------------------------------


class TestBugC2NoDeadlockOnEnvOverrideUnderLock:
    """The deadlock that nearly shipped: ``_audit_env_overrides`` was
    called from inside ``rebuild_engine_v2``'s ``_lock`` scope. If the
    audit_logger singleton happened to be uninitialized AND any ENV
    override was set, ``get_audit_logger()`` reentered
    ``get_config_v2()`` which tried to grab the same lock → deadlock.

    The repro requires (a) wiped singleton, (b) ENV override present.
    With a non-reentrant ``threading.Lock`` this hangs forever; we
    guard against regression by setting a short timeout via a watcher
    thread (signal.alarm doesn't work on Windows)."""

    def test_rebuild_with_env_override_and_reset_singleton_completes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import threading

        # Trigger Phase C ENV path: any of the 4 registered overrides
        # works; pick AUTO_CONFIRM since it touches the lightest field.
        monkeypatch.setenv("OPENAKITA_AUTO_CONFIRM", "1")

        # Wipe singleton so _audit_env_overrides would otherwise be
        # forced to re-init via get_config_v2() under the lock.
        al.reset_audit_logger()

        policies = tmp_path / "POLICIES.yaml"
        policies.write_text(_VALID_YAML, encoding="utf-8")

        result: dict[str, bool] = {"done": False}

        def _rebuild() -> None:
            global_engine.rebuild_engine_v2(yaml_path=policies)
            result["done"] = True

        worker = threading.Thread(target=_rebuild, daemon=True)
        worker.start()
        # 5 seconds is generous — a sound rebuild completes in <100ms.
        # If we deadlock, we'd hit timeout and fail with a clear msg.
        worker.join(timeout=5.0)

        assert result["done"], (
            "BUG-C2 regression: _audit_env_overrides re-entered "
            "get_config_v2() under _lock → deadlock. rebuild_engine_v2 "
            "must complete even when singleton is wiped + ENV override "
            "is set."
        )

    def test_repeated_rebuild_with_singleton_reset_stays_live(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Stress: do 10 rebuilds back-to-back, resetting the singleton
        between each. Any per-call lock leak would compound here."""
        import threading

        monkeypatch.setenv("OPENAKITA_AUTO_CONFIRM", "1")
        policies = tmp_path / "POLICIES.yaml"
        policies.write_text(_VALID_YAML, encoding="utf-8")

        success_count = 0

        def _rebuild_once(target_path: Path, signal: threading.Event) -> None:
            global_engine.rebuild_engine_v2(yaml_path=target_path)
            signal.set()

        for _i in range(10):
            al.reset_audit_logger()
            done = threading.Event()
            t = threading.Thread(
                target=_rebuild_once, args=(policies, done), daemon=True
            )
            t.start()
            if done.wait(timeout=3.0):
                success_count += 1

        assert success_count == 10, (
            f"expected 10/10 rebuilds, got {success_count} — "
            "lock leak or partial deadlock"
        )


class TestRegularFlowStillWorks:
    def test_valid_reload_with_existing_lkg_still_succeeds(
        self, tmp_path: Path
    ) -> None:
        policies = tmp_path / "POLICIES.yaml"
        policies.write_text(_VALID_YAML, encoding="utf-8")
        global_engine.rebuild_engine_v2(yaml_path=policies)
        before_lkg = global_engine._get_last_known_good()
        assert before_lkg is not None

        events: list[tuple[bool, str]] = []
        reloader = PolicyHotReloader(
            policies,
            poll_interval_seconds=1.0,
            debounce_seconds=0.0,
            on_reload=lambda ok, reason: events.append((ok, reason)),
        )

        import os
        import time

        policies.write_bytes(
            b"version: 2\n"
            b"security:\n"
            b"  workspace:\n"
            b"    paths: ['${CWD}', '/tmp']\n"
        )
        new_mtime = time.time() + 2.0
        os.utime(policies, (new_mtime, new_mtime))

        reloader._check_once()

        assert len(events) == 1
        ok, reason = events[0]
        assert ok is True
        assert "rebuilt" in reason

    def test_invalid_reload_with_existing_lkg_keeps_lkg(
        self, tmp_path: Path
    ) -> None:
        policies = tmp_path / "POLICIES.yaml"
        policies.write_text(_VALID_YAML, encoding="utf-8")
        global_engine.rebuild_engine_v2(yaml_path=policies)
        good_lkg = global_engine._get_last_known_good()
        assert good_lkg is not None

        events: list[tuple[bool, str]] = []
        reloader = PolicyHotReloader(
            policies,
            poll_interval_seconds=1.0,
            debounce_seconds=0.0,
            on_reload=lambda ok, reason: events.append((ok, reason)),
        )

        import os
        import time

        policies.write_bytes(_INVALID_YAML.encode())
        new_mtime = time.time() + 2.0
        os.utime(policies, (new_mtime, new_mtime))

        reloader._check_once()

        assert len(events) == 1
        ok, reason = events[0]
        assert ok is False
        assert "kept last-known-good" in reason
        assert global_engine._get_last_known_good() is good_lkg
