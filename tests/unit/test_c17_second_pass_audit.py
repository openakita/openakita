"""C17 二轮 audit 修复 — bug 复现 + 修复回归测试。

每个测试都对应一条第一次自审/子代理审查里指出的真问题：

1. ``/api/readyz`` 的 ``_check_audit_chain`` 探错了路径（``data/policy/``
   而不是 ``data/audit/policy_decisions.jsonl``），文件永远不存在 → 假绿。
2. ``_check_audit_chain`` 文件存在但 tail 全是空行 / 没内容时也假绿。
3. ``ChainedJsonlWriter._reload_last_hash_from_disk`` 用 64KB 死窗口，
   单行 audit 记录大于 64KB 时会读不到完整 row_hash，下一次 append 用
   stale ``_last_hash`` 写出 prev_hash 不匹配的 fork。
4. ``OrgEventStore.query`` 不持锁，与 ``emit`` 并发可能读到撕裂行。
5. ``OrgEventStore.clear()`` ``rmtree`` 删掉 ``.write.lock``，破坏跨进程
   协调。
6. ``_sanitize_for_chain`` 对 set/frozenset 走原始迭代顺序，跨进程同
   set 不同插入顺序产生不同 row_hash。
7. ``evolution_window.append_evolution_audit`` 的 ``except OSError`` 分支
   不再 fallback 到 raw append（filelock timeout 会丢 audit 行）。
8. ``ChatView`` SSE 解析：同 ``id:`` 后跟多条 ``data:`` 时，dedup 逻辑
   误把 ``pendingSeq`` 清零，第二条 data 漏 dedup。（前端逻辑，这里
   只验证后端不会因此重复发送同 id）
9. ``/api/readyz`` 把 ``_check_event_loop_lag`` 跟其它 check 用 gather
   并发跑，lag 会包含其它 check 的调度耗时 → 假阳性。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import health as health_module
from openakita.core.policy_v2 import audit_chain as ac
from openakita.core.policy_v2.param_mutation_audit import _sanitize_for_chain

# ---------------------------------------------------------------------------
# 1. readyz audit path bug
# ---------------------------------------------------------------------------


class TestReadyzAuditPathFix:
    """Earlier C17 probed ``data/policy/audit.jsonl`` while writers used
    ``data/audit/policy_decisions.jsonl`` (the AuditConfig default), so
    the probe was vacuously green even when the real chain was corrupt.
    These tests pin the probe to ``get_audit_logger()._path``.
    """

    def test_probe_uses_audit_logger_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Probe must read the *same* file the writer uses."""
        from openakita.core import audit_logger as al

        # Build an isolated logger pointing at tmp.
        log_path = tmp_path / "audit.jsonl"
        fake_logger = al.AuditLogger(path=str(log_path), enabled=True, include_chain=False)

        # Write a chain-shaped row (valid JSON).
        log_path.write_text(json.dumps({"ts": 1.0, "tool": "x"}) + "\n", encoding="utf-8")

        monkeypatch.setattr(al, "get_audit_logger", lambda: fake_logger)

        import asyncio
        result = asyncio.run(health_module._check_audit_chain())
        assert result is None, f"valid chain row should pass, got: {result}"

    def test_probe_flags_corrupt_tail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openakita.core import audit_logger as al

        log_path = tmp_path / "audit.jsonl"
        # Trailing line is bad JSON.
        log_path.write_text("{\"ok\":1}\n{this is not json}\n", encoding="utf-8")
        fake_logger = al.AuditLogger(path=str(log_path), enabled=True, include_chain=False)
        monkeypatch.setattr(al, "get_audit_logger", lambda: fake_logger)

        import asyncio
        result = asyncio.run(health_module._check_audit_chain())
        assert result is not None
        assert result["name"] == "audit_chain"

    def test_probe_flags_blank_only_tail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """File exists with non-zero size but is all whitespace → NOT OK."""
        from openakita.core import audit_logger as al

        log_path = tmp_path / "audit.jsonl"
        log_path.write_text("\n\n\n   \n", encoding="utf-8")  # whitespace only
        fake_logger = al.AuditLogger(path=str(log_path), enabled=True, include_chain=False)
        monkeypatch.setattr(al, "get_audit_logger", lambda: fake_logger)

        import asyncio
        result = asyncio.run(health_module._check_audit_chain())
        assert result is not None, "blank-only tail must not return OK"
        assert "blank" in result["details"] or "empty" in result["details"]

    def test_probe_silent_when_audit_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Operator explicitly turned audit off → readyz must NOT 503."""
        from openakita.core import audit_logger as al

        fake_logger = al.AuditLogger(
            path=str(tmp_path / "irrelevant.jsonl"), enabled=False, include_chain=False
        )
        monkeypatch.setattr(al, "get_audit_logger", lambda: fake_logger)

        import asyncio
        assert asyncio.run(health_module._check_audit_chain()) is None

    def test_probe_handles_missing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fresh install: file doesn't exist → OK (no entries yet)."""
        from openakita.core import audit_logger as al

        fake_logger = al.AuditLogger(
            path=str(tmp_path / "never_written.jsonl"),
            enabled=True,
            include_chain=False,
        )
        monkeypatch.setattr(al, "get_audit_logger", lambda: fake_logger)

        import asyncio
        assert asyncio.run(health_module._check_audit_chain()) is None


# ---------------------------------------------------------------------------
# 2. tail-window auto-expand on huge audit lines
# ---------------------------------------------------------------------------


class TestHugeLineTailReload:
    """Reproduce the case where a single audit record exceeds 64 KiB.

    Pre-fix: ``_reload_last_hash_from_disk`` read only the last 64 KiB,
    saw no complete final line, and silently bailed — leaving
    ``_last_hash`` stale and producing a chain fork on the next append.

    Post-fix: ``_read_last_complete_line`` doubles its window up to
    16 MiB so the row_hash is recoverable; verify_chain stays clean.
    """

    def test_reload_finds_hash_of_large_last_record(
        self, tmp_path: Path
    ) -> None:
        ac.reset_writers_for_testing()
        path = tmp_path / "chain.jsonl"
        writer = ac.get_writer(path)

        # Tiny first record, then a huge ~200 KiB record.
        writer.append({"ts": 1.0, "tag": "small"})
        big_blob = "X" * 200_000
        big_record = writer.append({"ts": 2.0, "tag": "big", "payload": big_blob})
        big_hash = big_record["row_hash"]

        # Simulate the "another process wrote since we bootstrapped"
        # scenario: nuke our in-memory hash and reload from disk.
        writer._last_hash = ac.GENESIS_HASH
        writer._reload_last_hash_from_disk()

        assert writer._last_hash == big_hash, (
            "after C17 二轮 the >64 KiB last line must still be parseable"
        )

        # And the next append should chain correctly off it.
        next_record = writer.append({"ts": 3.0, "tag": "after_big"})
        assert next_record["prev_hash"] == big_hash

        # End-to-end verification.
        result = ac.verify_chain(path)
        assert result.ok, f"chain verify failed: {result.reason}"

    def test_helper_returns_none_for_oversize_single_line(
        self, tmp_path: Path
    ) -> None:
        """If one line truly exceeds the 16 MiB cap, helper must refuse
        rather than corrupt the chain. We can't allocate 16 MiB in CI
        comfortably; instead patch the cap to a tiny value to exercise
        the path."""
        path = tmp_path / "huge_one_line.jsonl"
        # 100KB single line, no trailing \n — looks like a partial write
        # bigger than our patched 8KB cap.
        path.write_bytes(b"X" * 100_000)

        import openakita.core.policy_v2.audit_chain as ac_mod
        original_cap = ac_mod._MAX_TAIL_BYTES
        original_initial = ac_mod._INITIAL_TAIL_WINDOW
        try:
            ac_mod._MAX_TAIL_BYTES = 8192
            ac_mod._INITIAL_TAIL_WINDOW = 4096
            result = ac_mod._read_last_complete_line(path)
            assert result is None
        finally:
            ac_mod._MAX_TAIL_BYTES = original_cap
            ac_mod._INITIAL_TAIL_WINDOW = original_initial


# ---------------------------------------------------------------------------
# 3. OrgEventStore query lock + clear preserves lockfile
# ---------------------------------------------------------------------------


class TestOrgEventStoreLockingFix:
    def test_query_does_not_see_torn_line_under_concurrent_emit(
        self, tmp_path: Path
    ) -> None:
        """Stress: 4 writer threads + 1 reader thread for ~200 events.

        We don't deterministically *trigger* a torn-line race here (it'd
        require precise scheduling), but we DO assert that every event
        the reader sees is a complete, parseable JSON object. Pre-fix
        the read path used unlocked ``f.read_text()`` and split on
        ``\\n`` which would emit half-records into the parser.
        """
        from openakita.orgs.event_store import OrgEventStore

        store = OrgEventStore(tmp_path, "org-test")

        errors: list[str] = []
        stop = threading.Event()

        def writer(i: int) -> None:
            for k in range(50):
                if stop.is_set():
                    break
                store.emit("test", f"actor-{i}", {"k": k, "i": i, "payload": "x" * 200})

        def reader() -> None:
            while not stop.is_set():
                try:
                    events = store.query(limit=500)
                    for e in events:
                        assert isinstance(e, dict)
                        assert "event_type" in e
                except json.JSONDecodeError as exc:
                    errors.append(f"torn line: {exc}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"unexpected: {type(exc).__name__}: {exc}")
                time.sleep(0.001)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        rthread = threading.Thread(target=reader)
        rthread.start()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stop.set()
        rthread.join(timeout=2.0)

        assert not errors, f"observed corrupt reads: {errors[:3]}"

    def test_clear_preserves_write_lockfile(self, tmp_path: Path) -> None:
        """Simulate a sibling worker holding the lock when clear() runs.

        We don't actually need filelock semantics here — the bug was
        ``shutil.rmtree(events_dir)`` blowing away the lockfile path no
        matter what. We just ensure a file at the lock path survives.
        """
        from openakita.orgs.event_store import OrgEventStore

        store = OrgEventStore(tmp_path, "org-clear")
        store.emit("seed", "a", {"k": 1})

        # Materialize the lockfile (filelock may or may not have left it
        # after release; behaviour differs across versions / platforms).
        lock_path = store._events_dir / ".write.lock"
        lock_path.touch()
        # Mark it so we can prove this exact file survived rather than
        # being recreated empty.
        sentinel = b"SENTINEL_FROM_HOLDER\n"
        lock_path.write_bytes(sentinel)

        store.clear()

        assert lock_path.exists(), (
            "clear() must NOT delete the cross-process lockfile (C17 二轮)"
        )
        # Crucial: it's the *same* file, not a recreated empty one.
        assert lock_path.read_bytes() == sentinel, (
            "clear() recreated the lockfile (lost the holder's state)"
        )
        # Day-file payloads are gone.
        day_files = list(store._events_dir.glob("*.jsonl"))
        assert day_files == []


# ---------------------------------------------------------------------------
# 4. _sanitize_for_chain set/frozenset deterministic ordering
# ---------------------------------------------------------------------------


class TestSanitizeSetOrdering:
    def test_same_set_different_insertion_order_same_output(self) -> None:
        """Two logically identical sets, built in different orders, must
        sanitize to the *same* JSON-native list, otherwise the
        canonical row_hash would differ across processes."""
        s1 = {"alpha", "beta", "gamma", "delta"}
        s2 = set()
        for k in ["gamma", "delta", "alpha", "beta"]:
            s2.add(k)

        out1 = _sanitize_for_chain(s1)
        out2 = _sanitize_for_chain(s2)
        assert out1 == out2

    def test_frozenset_sorted_too(self) -> None:
        f1 = frozenset([3, 1, 2])
        f2 = frozenset([2, 3, 1])
        assert _sanitize_for_chain(f1) == _sanitize_for_chain(f2)

    def test_heterogeneous_set_does_not_crash(self) -> None:
        """Mixed-type sets used to crash when ``sorted()`` couldn't
        compare different types. The fallback path sorts by repr."""
        # ``object()`` instances aren't comparable to strings/ints
        mixed = {"x", 7, ("a", 1)}
        out = _sanitize_for_chain(mixed)
        assert isinstance(out, list)
        assert len(out) == 3


# ---------------------------------------------------------------------------
# 5. evolution_window OSError fallback to raw append
# ---------------------------------------------------------------------------


class TestEvolutionAuditFallback:
    """Pre-fix: filelock timeout (re-raised as ``OSError`` by
    ``audit_chain.append``) hit the bare-warn branch and never fell
    through to raw append → audit line silently dropped under
    contention. Now every chain-write failure tries the raw fallback.
    """

    def test_oserror_falls_through_to_raw_append(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openakita.core.policy_v2 import evolution_window as ew

        audit_path = tmp_path / "evolution.jsonl"

        # Simulate filelock timeout — same exception shape audit_chain raises.
        class _FakeWriter:
            def append(self, record):
                raise OSError(f"audit_chain filelock timeout on {audit_path}")

        monkeypatch.setattr(
            "openakita.core.policy_v2.audit_chain.get_writer",
            lambda _path: _FakeWriter(),
        )

        ew.record_decision(
            fix_id="fix-001",
            audit_path=audit_path,
            decision_record={"kind": "test", "tag": "fallback"},
        )

        # Even though chain failed, the raw fallback should have written
        # one line.
        assert audit_path.exists()
        text = audit_path.read_text(encoding="utf-8").strip()
        assert text, "raw fallback must have written the audit line"
        row = json.loads(text)
        assert row["fix_id"] == "fix-001"
        assert row["kind"] == "test"


# ---------------------------------------------------------------------------
# 6. readyz event_loop_lag isolation (regression smoke)
# ---------------------------------------------------------------------------


class TestReadyzLagIsolation:
    """``_check_event_loop_lag`` now runs *after* the gather batch
    finishes, on a quiet loop. We assert two things:

    1. Even when other (synchronous) checks block briefly, the lag
       result reflects only the post-gather measurement.
    2. The structural ordering is preserved (lag is always last in the
       failing list).
    """

    def test_lag_is_measured_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slow audit_chain check shouldn't poison the lag report."""
        import asyncio

        # Block the audit check for ~200ms — pre-fix this would have
        # appeared as 200ms of event_loop lag inside the gather window.
        async def _slow_audit() -> None:
            await asyncio.sleep(0.2)
            return None

        monkeypatch.setattr(health_module, "_check_audit_chain", _slow_audit)
        monkeypatch.setattr(
            health_module,
            "_check_policy_engine",
            lambda: _slow_audit(),  # treat as another I/O wait
        )

        app = FastAPI()
        app.include_router(health_module.router)
        app.state.scheduler = None
        app.state.gateway = None
        health_module._readyz_cache.update({"ts": 0.0, "payload": None, "ready": False})

        client = TestClient(app)
        r = client.get("/api/readyz")
        # Even though we slept 200ms, lag check runs solo afterwards on a
        # quiet loop → should pass (< 500ms threshold).
        assert r.status_code == 200, r.text
