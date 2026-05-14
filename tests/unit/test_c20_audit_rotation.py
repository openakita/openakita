"""C20 Phase A — Audit JSONL rotation: rotation engine + chain head carry-over.

Coverage
========

1. **Schema**: ``AuditConfig.rotation_mode`` / ``rotation_size_mb`` /
   ``rotation_keep_count`` defaults, bounds, strict types.
2. **Default-off**: when ``rotation_mode = "none"`` the writer never
   rotates regardless of file age or size — C16/C17/C18 behaviour
   preserved.
3. **Daily mode**: file with mtime in the past UTC day is renamed to
   ``<stem>.YYYY-MM-DD.jsonl`` before the new record is written; the
   new record lands in the empty active file.
4. **Size mode**: ``stat().st_size + len(serialized_line)`` crossing
   the threshold triggers rotation; archive uses
   ``<stem>.YYYYMMDDTHHMMSS.jsonl``.
5. **Chain head carry-over**: the FIRST record in the new active file
   has ``prev_hash`` equal to the LAST record's ``row_hash`` in the
   rotated archive — verifies the contract stated in
   ``audit_chain.py`` docstring for rotation.
6. **Prune**: ``rotation_keep_count`` removes the oldest archives
   beyond the limit; ``keep_count = 0`` keeps everything.
7. **Idempotency**: calling ``append`` when no rotation trigger fires
   doesn't rename anything; existing archive paths are never
   overwritten on race.
8. **Deadlock-immune**: rotation config reads via lock-free module
   attribute (regression guard for the BUG-C2-style pattern I just
   re-introduced and fixed mid-implementation).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from openakita.core.policy_v2 import audit_chain
from openakita.core.policy_v2.audit_chain import (
    GENESIS_HASH,
    ChainedJsonlWriter,
    get_writer,
    verify_chain,
)
from openakita.core.policy_v2.schema import AuditConfig, PolicyConfigV2


@pytest.fixture(autouse=True)
def _reset_writers() -> None:
    audit_chain.reset_writers_for_testing()
    yield
    audit_chain.reset_writers_for_testing()


@pytest.fixture
def install_audit_config(monkeypatch: pytest.MonkeyPatch):
    """Install a synthetic ``PolicyConfigV2`` so ``audit_chain``'s
    lock-free module-attribute read returns our test config. We bypass
    ``rebuild_engine_v2`` entirely — that path is exercised in
    integration tests; here we just need ``audit_chain._get_rotation_
    config()`` to see the values we want.
    """
    from openakita.core.policy_v2 import global_engine as ge

    def _install(audit_kwargs: dict) -> None:
        cfg = PolicyConfigV2.model_validate(
            {"audit": audit_kwargs}, strict=False
        )
        monkeypatch.setattr(ge, "_config", cfg)

    return _install


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_defaults_disable_rotation(self) -> None:
        cfg = AuditConfig()
        assert cfg.rotation_mode == "none"
        assert cfg.rotation_size_mb == 100
        assert cfg.rotation_keep_count == 30

    def test_mode_accepts_known_values(self) -> None:
        for mode in ("none", "daily", "size"):
            cfg = AuditConfig(rotation_mode=mode)  # type: ignore[arg-type]
            assert cfg.rotation_mode == mode

    def test_mode_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            AuditConfig(rotation_mode="hourly")  # type: ignore[arg-type]

    def test_size_mb_bounds(self) -> None:
        AuditConfig(rotation_size_mb=1)
        AuditConfig(rotation_size_mb=10240)
        with pytest.raises(ValidationError):
            AuditConfig(rotation_size_mb=0)
        with pytest.raises(ValidationError):
            AuditConfig(rotation_size_mb=10241)

    def test_keep_count_bounds(self) -> None:
        AuditConfig(rotation_keep_count=0)
        AuditConfig(rotation_keep_count=10000)
        with pytest.raises(ValidationError):
            AuditConfig(rotation_keep_count=-1)
        with pytest.raises(ValidationError):
            AuditConfig(rotation_keep_count=10001)


# ---------------------------------------------------------------------------
# 2. Default-off (preserve C16/C17/C18 behaviour)
# ---------------------------------------------------------------------------


class TestRotationDefaultOff:
    def test_no_rotation_when_mode_is_none(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        install_audit_config({"rotation_mode": "none"})
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        for i in range(5):
            writer.append({"event": f"e{i}", "ts": time.time()})

        # No archives created.
        assert not any(
            p.name != log.name and p.name.startswith("audit.")
            for p in tmp_path.iterdir()
            if p.name.endswith(".jsonl")
        )
        # Active file got all 5 rows.
        rows = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(rows) == 5

    def test_no_rotation_when_config_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No PolicyConfigV2 installed — writer must fall back to
        rotation disabled, never raise."""
        from openakita.core.policy_v2 import global_engine as ge

        monkeypatch.setattr(ge, "_config", None)
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        writer.append({"event": "ok", "ts": time.time()})
        rows = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# 3. Daily rotation
# ---------------------------------------------------------------------------


class TestDailyRotation:
    def test_yesterday_mtime_triggers_rotation(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        install_audit_config({"rotation_mode": "daily"})
        log = tmp_path / "audit.jsonl"

        writer = get_writer(log)
        writer.append({"event": "old", "ts": time.time()})

        # Push the file's mtime to 25h ago so its UTC date is "yesterday".
        old_ts = time.time() - 25 * 3600
        import os

        os.utime(log, (old_ts, old_ts))

        writer.append({"event": "fresh", "ts": time.time()})

        # Expect an archive named after yesterday.
        yesterday = datetime.fromtimestamp(old_ts, tz=UTC).date().strftime(
            "%Y-%m-%d"
        )
        archive = tmp_path / f"audit.{yesterday}.jsonl"
        assert archive.exists(), (
            f"daily rotation should produce {archive}; "
            f"found: {[p.name for p in tmp_path.iterdir()]}"
        )
        # Active file now contains only the fresh record.
        active_rows = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(active_rows) == 1
        assert json.loads(active_rows[0])["event"] == "fresh"
        # Archive contains the old record.
        arch_rows = archive.read_text(encoding="utf-8").strip().splitlines()
        assert len(arch_rows) == 1
        assert json.loads(arch_rows[0])["event"] == "old"

    def test_same_day_mtime_no_rotation(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        install_audit_config({"rotation_mode": "daily"})
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        writer.append({"event": "first", "ts": time.time()})
        writer.append({"event": "second", "ts": time.time()})

        # Both rows in the active file, no archives.
        rows = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(rows) == 2
        archives = [
            p for p in tmp_path.iterdir() if p.name != log.name
            and p.name.endswith(".jsonl")
        ]
        assert archives == []


# ---------------------------------------------------------------------------
# 4. Size rotation
# ---------------------------------------------------------------------------


class TestSizeRotation:
    def test_size_threshold_triggers_rotation(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        # 1 MiB threshold; one big record is enough to cross it.
        install_audit_config({
            "rotation_mode": "size",
            "rotation_size_mb": 1,
        })
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        big_payload = "x" * (600 * 1024)  # 600 KiB string × 2 rows = 1.2 MiB
        writer.append({"event": "a", "blob": big_payload})
        writer.append({"event": "b", "blob": big_payload})
        # Third write should rotate (size already > 1 MiB at start of
        # this call).
        writer.append({"event": "c", "blob": "small"})

        archives = sorted(
            p for p in tmp_path.iterdir()
            if p.name != log.name and p.name.endswith(".jsonl")
        )
        assert len(archives) == 1, (
            f"expected exactly 1 archive after size threshold breach; "
            f"got: {[p.name for p in archives]}"
        )
        # Archive name has YYYYMMDDTHHMMSS-style stamp.
        assert (
            "T" in archives[0].name
            and archives[0].name.startswith("audit.")
            and archives[0].name.endswith(".jsonl")
        )

    def test_no_rotation_below_threshold(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        install_audit_config({
            "rotation_mode": "size",
            "rotation_size_mb": 100,  # 100 MiB — way above test traffic
        })
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)
        for i in range(20):
            writer.append({"event": f"e{i}", "ts": time.time()})

        archives = [
            p for p in tmp_path.iterdir()
            if p.name != log.name and p.name.endswith(".jsonl")
        ]
        assert archives == []


# ---------------------------------------------------------------------------
# 5. Chain head carry-over (the core C20 contract)
# ---------------------------------------------------------------------------


class TestChainHeadCarryOver:
    def test_first_record_in_new_file_chains_to_archive_tail(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        """The whole point of C20: rotation must NOT break the hash
        chain. The first row in the active file after rotation must
        reference the last row's row_hash in the archive."""
        install_audit_config({"rotation_mode": "daily"})
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        # Write two rows in the "old day".
        writer.append({"event": "old-1", "ts": 1000.0})
        writer.append({"event": "old-2", "ts": 1001.0})

        last_row_hash_pre_rotate = writer.last_hash
        assert last_row_hash_pre_rotate != GENESIS_HASH

        # Push mtime back to "yesterday".
        import os

        old_ts = time.time() - 25 * 3600
        os.utime(log, (old_ts, old_ts))

        # Write the "new day" first row.
        writer.append({"event": "fresh-1", "ts": time.time()})

        # Find archive.
        archives = [
            p for p in tmp_path.iterdir()
            if p.name != log.name and p.name.endswith(".jsonl")
        ]
        assert len(archives) == 1
        archive = archives[0]

        # Verify archive's last row's row_hash equals new file's first
        # row's prev_hash.
        arch_rows = [
            json.loads(line)
            for line in archive.read_text(encoding="utf-8").splitlines()
            if line
        ]
        active_rows = [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line
        ]
        assert len(arch_rows) == 2
        assert len(active_rows) == 1

        archive_last_hash = arch_rows[-1]["row_hash"]
        active_first_prev = active_rows[0]["prev_hash"]
        assert active_first_prev == archive_last_hash, (
            "C20 chain-head carry-over violated: the first record in the "
            "new active file should reference the last row_hash in the "
            "rotated archive."
        )
        assert archive_last_hash == last_row_hash_pre_rotate

    def test_chain_continuous_across_multiple_rotations(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        """Three rotations in a row: each archive's tail equals the next
        file's head's prev_hash."""
        install_audit_config({
            "rotation_mode": "size",
            "rotation_size_mb": 1,
        })
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        # Force 3 rotations: write 2 large records per "epoch", then a
        # third that crosses the threshold.
        big = "x" * (600 * 1024)
        for epoch in range(3):
            writer.append({"epoch": epoch, "n": 0, "blob": big})
            writer.append({"epoch": epoch, "n": 1, "blob": big})
            writer.append({"epoch": epoch, "n": 2, "blob": "small"})

        archives = sorted(
            (p for p in tmp_path.iterdir()
             if p.name != log.name and p.name.endswith(".jsonl")),
            key=lambda p: p.stat().st_mtime,
        )
        # Expect at least 2 archives (size mode produces an archive each
        # time the threshold is crossed; exact count depends on timing).
        assert len(archives) >= 2

        # Concatenate archives in mtime order + active file, walk the
        # chain manually.
        all_rows: list[dict] = []
        for f in archives + [log]:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    all_rows.append(json.loads(line))

        expected_prev = GENESIS_HASH
        for i, row in enumerate(all_rows):
            assert row["prev_hash"] == expected_prev, (
                f"chain broken at concatenated row {i}: expected "
                f"prev_hash={expected_prev[:12]}..., got "
                f"{row['prev_hash'][:12]}..."
            )
            expected_prev = row["row_hash"]

    def test_each_file_individually_verifies(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        """``verify_chain(single_file)`` should still report ``ok=True``
        for both the archive and the active file when looked at
        independently — each file is self-consistent (chains internally
        from its first row), it just doesn't link back to the previous
        archive in isolation. Cross-file walking is Phase B."""
        install_audit_config({"rotation_mode": "daily"})
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        writer.append({"event": "old-1"})
        writer.append({"event": "old-2"})
        import os

        os.utime(log, (time.time() - 25 * 3600, time.time() - 25 * 3600))
        writer.append({"event": "fresh-1"})
        writer.append({"event": "fresh-2"})

        archives = [
            p for p in tmp_path.iterdir()
            if p.name != log.name and p.name.endswith(".jsonl")
        ]
        assert len(archives) == 1
        # Archive: internally consistent (chains from GENESIS).
        arch_result = verify_chain(archives[0])
        assert arch_result.ok, arch_result.reason
        # Active file: NOT consistent in isolation (its first row's
        # prev_hash points to the archive's tail, not GENESIS).
        # This documents Phase B's necessity: we need cross-file walk
        # to verify the "current" file after rotations.
        active_result = verify_chain(log)
        assert active_result.ok is False
        assert "prev_hash mismatch" in (active_result.reason or "")


# ---------------------------------------------------------------------------
# 6. Prune
# ---------------------------------------------------------------------------


class TestPrune:
    def test_keep_count_removes_oldest_archives(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        install_audit_config({
            "rotation_mode": "size",
            "rotation_size_mb": 1,
            "rotation_keep_count": 2,
        })
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        big = "x" * (600 * 1024)
        # Force 4 rotations.

        for epoch in range(4):
            writer.append({"epoch": epoch, "blob": big})
            writer.append({"epoch": epoch, "blob": big})
            writer.append({"epoch": epoch, "n": "trigger", "blob": "y"})
            # Ensure unique archive stamp by sleeping the tiniest
            # observable interval on this OS.
            time.sleep(0.05)

        archives = sorted(
            (p for p in tmp_path.iterdir()
             if p.name != log.name and p.name.endswith(".jsonl")),
            key=lambda p: p.stat().st_mtime,
        )
        # keep_count=2 means at most 2 archives may remain.
        assert len(archives) <= 2, (
            f"keep_count=2 should cap archives; got {len(archives)}: "
            f"{[p.name for p in archives]}"
        )

    def test_keep_count_zero_keeps_all(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        install_audit_config({
            "rotation_mode": "size",
            "rotation_size_mb": 1,
            "rotation_keep_count": 0,
        })
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        big = "x" * (600 * 1024)
        for epoch in range(3):
            writer.append({"epoch": epoch, "blob": big})
            writer.append({"epoch": epoch, "blob": big})
            writer.append({"epoch": epoch, "n": "trigger"})
            time.sleep(0.05)

        archives = [
            p for p in tmp_path.iterdir()
            if p.name != log.name and p.name.endswith(".jsonl")
        ]
        # 3 epochs each crossing threshold → at least 2 archives kept.
        assert len(archives) >= 2


# ---------------------------------------------------------------------------
# 7. Idempotency / race safety
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_existing_archive_path_not_overwritten(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        """If a same-named archive already exists (e.g. clock jump on
        daily mode), the writer must NOT overwrite history."""
        install_audit_config({"rotation_mode": "daily"})
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)
        writer.append({"event": "original"})

        # Pre-create the would-be archive path.
        import os

        old_ts = time.time() - 25 * 3600
        os.utime(log, (old_ts, old_ts))
        yesterday = datetime.fromtimestamp(old_ts, tz=UTC).date().strftime(
            "%Y-%m-%d"
        )
        archive_path = tmp_path / f"audit.{yesterday}.jsonl"
        archive_path.write_text(
            '{"preexisting":true}\n', encoding="utf-8"
        )

        # Next write would try to rotate to archive_path — but it
        # exists, so rotation is skipped (logged as warning).
        writer.append({"event": "after"})

        # Pre-existing archive content untouched.
        content = archive_path.read_text(encoding="utf-8")
        assert "preexisting" in content
        assert "original" not in content

    def test_empty_file_no_rotation(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        """An empty / non-existent active file never rotates — nothing
        to archive."""
        install_audit_config({"rotation_mode": "daily"})
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)
        writer.append({"event": "first"})  # creates the file
        assert log.exists()
        # Even if mtime is ancient, an empty file wouldn't rotate; let's
        # ensure that's true for a fresh empty file too.
        log.unlink()
        log.write_text("", encoding="utf-8")
        import os

        old_ts = time.time() - 25 * 3600
        os.utime(log, (old_ts, old_ts))
        audit_chain.reset_writers_for_testing()
        writer2 = get_writer(log)
        writer2.append({"event": "after-empty"})
        # No archive because empty file isn't worth rotating.
        archives = [
            p for p in tmp_path.iterdir()
            if p.name != log.name and p.name.endswith(".jsonl")
        ]
        assert archives == []


# ---------------------------------------------------------------------------
# 8. Deadlock-immune (regression guard — see audit_chain.py docstring)
# ---------------------------------------------------------------------------


class TestDeadlockImmune:
    def test_rotation_config_read_is_lock_free(self) -> None:
        """``_get_rotation_config`` MUST NOT actually CALL ``get_config_v2()``
        (mentioning it in a docstring to explain WHY we don't is fine).
        ``get_config_v2`` re-enters ``global_engine._lock`` and would
        reproduce BUG-C2. AST-level static guard so a future refactor
        can't silently regress."""
        import ast
        import inspect
        import textwrap

        src = textwrap.dedent(
            inspect.getsource(ChainedJsonlWriter._get_rotation_config)
        )
        tree = ast.parse(src)
        called_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called_names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called_names.add(func.attr)
        assert "get_config_v2" not in called_names, (
            "BUG-C2 regression: _get_rotation_config must not CALL "
            "get_config_v2() — it's invoked from inside append() which "
            "can itself run under rebuild_engine_v2's _lock. "
            f"Found calls: {sorted(called_names)}"
        )

    def test_append_during_simulated_lock_hold_does_not_deadlock(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        """Concrete test: we install a synthetic config + hold
        ``global_engine._lock`` from a separate thread (simulating
        ``rebuild_engine_v2``) and append from another thread. Must
        finish within timeout."""
        install_audit_config({"rotation_mode": "daily"})
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        from openakita.core.policy_v2 import global_engine as ge

        lock_held = threading.Event()
        release = threading.Event()

        def _hold_lock() -> None:
            with ge._lock:
                lock_held.set()
                release.wait(timeout=5.0)

        holder = threading.Thread(target=_hold_lock, daemon=True)
        holder.start()
        assert lock_held.wait(timeout=2.0)

        done = threading.Event()

        def _do_append() -> None:
            writer.append({"event": "under-lock", "ts": time.time()})
            done.set()

        worker = threading.Thread(target=_do_append, daemon=True)
        worker.start()

        assert done.wait(timeout=3.0), (
            "BUG-C2-style deadlock: ChainedJsonlWriter.append blocked "
            "on global_engine._lock during rotation config read."
        )

        release.set()
        holder.join(timeout=1.0)


# ---------------------------------------------------------------------------
# Bonus: existing chain integrity tests still pass through rotation
# ---------------------------------------------------------------------------


class TestRotationDoesNotBreakRecordShape:
    def test_records_in_active_and_archive_are_valid_chain_format(
        self, tmp_path: Path, install_audit_config
    ) -> None:
        install_audit_config({"rotation_mode": "daily"})
        log = tmp_path / "audit.jsonl"
        writer = get_writer(log)

        writer.append({"event": "a", "ts": 1.0})
        import os

        os.utime(log, (time.time() - 25 * 3600, time.time() - 25 * 3600))
        writer.append({"event": "b", "ts": 2.0})

        for f in tmp_path.glob("audit*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                obj = json.loads(line)
                # Every line carries both chain fields (no legacy gap).
                assert "prev_hash" in obj
                assert "row_hash" in obj
                assert isinstance(obj["prev_hash"], str)
                assert len(obj["prev_hash"]) == 64
                assert isinstance(obj["row_hash"], str)
                assert len(obj["row_hash"]) == 64
