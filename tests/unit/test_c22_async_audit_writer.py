"""C22 P3-2: AsyncBatchAuditWriter regression + integration.

Background
==========

Plan §13.5.2 A required an asyncio.Queue-based audit writer to take the
filelock + tail-read cost off the engine hot path. Until C22 every
``AuditLogger.log()`` call sat in the synchronous ChainedJsonlWriter
path (~1-2 ms each on healthy disk, much worse under contention).

C22 P3-2 introduces ``AsyncBatchAuditWriter`` plus a
``ChainedJsonlWriter.append_batch`` primitive that coalesces N records
under a SINGLE filelock acquisition. ``AuditLogger`` now opportunistically
routes through the async writer when started, fallback sync otherwise.

Test scope
==========

1. ChainedJsonlWriter.append_batch chain correctness (vs N×append)
2. AsyncBatchAuditWriter lifecycle (start/stop idempotency, is_running)
3. enqueue from coroutine inside loop
4. enqueue from foreign thread (FastAPI worker pattern)
5. Batching behaviour (max_batch_size threshold + max_batch_delay timeout)
6. Backpressure: queue full → sync fallback (record still persisted)
7. Stop draining (records queued before stop ARE written)
8. AuditLogger integration: when async writer is up, log() routes via it
9. AuditLogger integration: when no async writer, sync path still works
10. Chain integrity preserved across mixed sync/async writes
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openakita.core.policy_v2 import audit_chain
from openakita.core.policy_v2 import audit_writer as aw_mod
from openakita.core.policy_v2.audit_chain import (
    ChainedJsonlWriter,
    reset_writers_for_testing,
    verify_chain,
)
from openakita.core.policy_v2.audit_writer import (
    AsyncBatchAuditWriter,
    reset_for_testing,
    start_global_audit_writer,
    stop_global_audit_writer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_singletons():
    """Each test gets a fresh writer singleton + fresh global async writer."""
    reset_writers_for_testing()
    reset_for_testing()
    yield
    reset_writers_for_testing()
    reset_for_testing()


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit_p32" / "policy_decisions.jsonl"


# ---------------------------------------------------------------------------
# 1. ChainedJsonlWriter.append_batch
# ---------------------------------------------------------------------------


class TestAppendBatchChainIntegrity:
    def test_batch_chain_equivalent_to_individual_append(self, tmp_path: Path) -> None:
        """N records via append_batch produce same hash chain as N append calls.

        This is THE correctness contract — anything else and the verifier
        will mark the file as tampered.
        """
        path_a = tmp_path / "by_one" / "audit.jsonl"
        path_b = tmp_path / "by_batch" / "audit.jsonl"
        records = [
            {"ts": 1.0 + i, "tool": "test_tool", "decision": "allow", "i": i}
            for i in range(5)
        ]

        reset_writers_for_testing()
        w_a = ChainedJsonlWriter(path_a)
        for r in records:
            w_a.append(r)

        reset_writers_for_testing()
        w_b = ChainedJsonlWriter(path_b)
        w_b.append_batch(records)

        a_lines = path_a.read_text(encoding="utf-8").strip().splitlines()
        b_lines = path_b.read_text(encoding="utf-8").strip().splitlines()
        assert len(a_lines) == len(b_lines) == 5

        for la, lb in zip(a_lines, b_lines, strict=True):
            obj_a = json.loads(la)
            obj_b = json.loads(lb)
            # row_hash and prev_hash must match byte-for-byte
            assert obj_a["row_hash"] == obj_b["row_hash"]
            assert obj_a["prev_hash"] == obj_b["prev_hash"]

        # Both chains independently verify
        assert verify_chain(path_a).ok
        assert verify_chain(path_b).ok

    def test_empty_batch_is_noop(self, audit_path: Path) -> None:
        writer = ChainedJsonlWriter(audit_path)
        out = writer.append_batch([])
        assert out == []
        assert not audit_path.exists() or audit_path.read_text() == ""

    def test_batch_rejects_pre_populated_chain_fields(self, audit_path: Path) -> None:
        writer = ChainedJsonlWriter(audit_path)
        with pytest.raises(ValueError, match="prev_hash"):
            writer.append_batch([{"ts": 1.0, "prev_hash": "x" * 64}])
        with pytest.raises(ValueError, match="row_hash"):
            writer.append_batch([{"ts": 1.0, "row_hash": "x" * 64}])

    def test_batch_rejects_non_dict(self, audit_path: Path) -> None:
        writer = ChainedJsonlWriter(audit_path)
        with pytest.raises(TypeError):
            writer.append_batch([{"ts": 1.0}, "not a dict"])  # type: ignore[list-item]

    def test_batch_after_existing_appends_continues_chain(self, audit_path: Path) -> None:
        """Mixed sync append + batch sequence must still verify."""
        writer = ChainedJsonlWriter(audit_path)
        writer.append({"ts": 1.0, "tool": "a"})
        writer.append({"ts": 2.0, "tool": "b"})
        writer.append_batch(
            [
                {"ts": 3.0, "tool": "c"},
                {"ts": 4.0, "tool": "d"},
                {"ts": 5.0, "tool": "e"},
            ]
        )
        writer.append({"ts": 6.0, "tool": "f"})

        result = verify_chain(audit_path)
        assert result.ok, result.reason
        assert result.total == 6


# ---------------------------------------------------------------------------
# 2. AsyncBatchAuditWriter lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop_is_running(self, audit_path: Path) -> None:
        w = AsyncBatchAuditWriter(str(audit_path))
        assert not w.is_running()
        await w.start()
        assert w.is_running()
        await w.stop()
        assert not w.is_running()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, audit_path: Path) -> None:
        w = AsyncBatchAuditWriter(str(audit_path))
        await w.start()
        first_task = w._worker_task
        await w.start()  # should NOT spawn a second worker
        assert w._worker_task is first_task
        await w.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, audit_path: Path) -> None:
        w = AsyncBatchAuditWriter(str(audit_path))
        await w.start()
        await w.stop()
        await w.stop()  # second stop is a no-op

    @pytest.mark.asyncio
    async def test_restart_after_stop(self, audit_path: Path) -> None:
        """After stop(), start() should bring the worker back up."""
        w = AsyncBatchAuditWriter(str(audit_path))
        await w.start()
        w.enqueue({"ts": 1.0, "tool": "first"})
        await w.flush()
        await w.stop()

        await w.start()
        w.enqueue({"ts": 2.0, "tool": "second"})
        await w.flush()
        await w.stop()

        result = verify_chain(audit_path)
        assert result.ok
        assert result.total == 2


# ---------------------------------------------------------------------------
# 3. enqueue from loop coroutine + foreign thread
# ---------------------------------------------------------------------------


class TestEnqueueFromVariousContexts:
    @pytest.mark.asyncio
    async def test_enqueue_from_loop_coroutine(self, audit_path: Path) -> None:
        w = AsyncBatchAuditWriter(str(audit_path), max_batch_size=4, max_batch_delay_ms=20)
        await w.start()
        try:
            for i in range(10):
                w.enqueue({"ts": float(i), "tool": f"t{i}"})
            await w.flush()
            assert w.stats["written"] == 10
            assert w.stats["enqueued"] >= 1  # at least one went through queue
            assert verify_chain(audit_path).ok
        finally:
            await w.stop()

    @pytest.mark.asyncio
    async def test_enqueue_from_foreign_thread(self, audit_path: Path) -> None:
        """Producers may be FastAPI worker threads or gateway tasks
        running outside the event loop's thread. ``enqueue`` must
        marshal via call_soon_threadsafe.

        NB on test wiring: we **must** use ``asyncio.to_thread`` rather
        than the raw ``threading.Thread`` + ``join`` pattern. The latter
        blocks the loop's only thread waiting for the producer thread,
        so ``call_soon_threadsafe`` callbacks never get a chance to
        fire until ``join`` returns — by which time ``queue.join()``
        in :meth:`flush` sees an empty queue and races us. ``to_thread``
        yields control back to the loop between thread steps, which is
        the realistic FastAPI worker pattern anyway.
        """
        w = AsyncBatchAuditWriter(str(audit_path), max_batch_size=4, max_batch_delay_ms=20)
        await w.start()

        def producer():
            for i in range(20):
                w.enqueue({"ts": float(i + 100), "tool": f"thread_{i}"})

        try:
            await asyncio.to_thread(producer)
            # Give the loop a tick to drain any pending call_soon_threadsafe
            # scheduling that the foreign thread queued.
            await asyncio.sleep(0)
            await w.flush()
            assert w.stats["written"] == 20
            assert verify_chain(audit_path).ok
        finally:
            await w.stop()


# ---------------------------------------------------------------------------
# 4. Batching behaviour
# ---------------------------------------------------------------------------


class TestBatching:
    @pytest.mark.asyncio
    async def test_batches_to_max_size(self, audit_path: Path) -> None:
        """Flooding the queue with >max_batch records should pack a
        full batch in one append_batch call, not 1 append per record."""
        w = AsyncBatchAuditWriter(
            str(audit_path), max_batch_size=10, max_batch_delay_ms=200
        )
        await w.start()
        try:
            for i in range(25):
                w.enqueue({"ts": float(i), "tool": f"t{i}"})
            await w.flush()
            # Expect roughly ceil(25/10) = 3 batches; allow ≤4 to absorb
            # scheduler racing on small inputs.
            assert 2 <= w.stats["batches"] <= 4, (
                f"expected 2-4 batches for 25 records with max=10, "
                f"got {w.stats['batches']}"
            )
            assert w.stats["written"] == 25
        finally:
            await w.stop()

    @pytest.mark.asyncio
    async def test_delay_timeout_flushes_partial_batch(self, audit_path: Path) -> None:
        """One record with a long max_delay should still flush within
        max_delay even though the batch hasn't filled. This is the
        latency upper bound."""
        w = AsyncBatchAuditWriter(
            str(audit_path), max_batch_size=100, max_batch_delay_ms=30
        )
        await w.start()
        try:
            w.enqueue({"ts": 1.0, "tool": "lonely"})
            # Wait for >max_delay; the worker should flush the lone record.
            await asyncio.sleep(0.1)
            assert w.stats["written"] == 1
        finally:
            await w.stop()


# ---------------------------------------------------------------------------
# 5. Backpressure
# ---------------------------------------------------------------------------


class TestBackpressure:
    @pytest.mark.asyncio
    async def test_queue_full_falls_back_to_sync_record_preserved(
        self, audit_path: Path
    ) -> None:
        """Tiny queue + fast producer → queue saturates → enqueue must
        sync-write the overflow record (NOT drop it)."""
        w = AsyncBatchAuditWriter(
            str(audit_path),
            max_batch_size=64,
            max_batch_delay_ms=100,
            queue_maxsize=2,
        )
        await w.start()
        try:
            # Hold the worker's loop with a sleep so the queue can fill.
            # Actually the worker pops from queue eagerly; the simplest
            # test is to enqueue many faster than the worker can drain.
            for i in range(100):
                w.enqueue({"ts": float(i), "tool": f"flood_{i}"})
            await w.flush()
            # Every record must be persisted (no drops). Sync fallback
            # path is allowed to be used.
            assert w.stats["written"] + w.stats["sync_fallback"] == 100
            # At least SOME records went the async path.
            assert w.stats["written"] > 0
            assert verify_chain(audit_path).ok
        finally:
            await w.stop()


# ---------------------------------------------------------------------------
# 6. Stop drain semantics
# ---------------------------------------------------------------------------


class TestStopDrain:
    @pytest.mark.asyncio
    async def test_stop_drains_in_flight_records(self, audit_path: Path) -> None:
        """Records enqueued just before stop() must reach disk."""
        w = AsyncBatchAuditWriter(
            str(audit_path), max_batch_size=4, max_batch_delay_ms=100
        )
        await w.start()
        for i in range(8):
            w.enqueue({"ts": float(i), "tool": f"drain_{i}"})
        # Don't flush — stop must do it.
        await w.stop()
        result = verify_chain(audit_path)
        assert result.ok
        assert result.total == 8


# ---------------------------------------------------------------------------
# 7. AuditLogger integration
# ---------------------------------------------------------------------------


class TestAuditLoggerIntegration:
    @pytest.mark.asyncio
    async def test_log_routes_through_async_writer_when_running(
        self, audit_path: Path
    ) -> None:
        from openakita.core.audit_logger import AuditLogger

        await start_global_audit_writer(str(audit_path))
        try:
            audit = AuditLogger(path=str(audit_path), enabled=True, include_chain=True)
            audit.log(
                tool_name="run_shell",
                decision="allow",
                reason="test path",
                params_preview="ls",
                metadata={"approval_class": "exec_low_risk"},
            )
            audit.log(
                tool_name="write_file",
                decision="confirm",
                reason="destructive write",
                params_preview="path=/x",
                metadata={"approval_class": "destructive"},
            )
            # Need a manual flush — AuditLogger.log returns immediately.
            from openakita.core.policy_v2.audit_writer import get_async_audit_writer

            w = get_async_audit_writer(str(audit_path))
            assert w is not None
            await w.flush()
            assert w.stats["written"] == 2
        finally:
            await stop_global_audit_writer()

        result = verify_chain(audit_path)
        assert result.ok
        assert result.total == 2

    def test_log_sync_path_works_when_no_async_writer(self, audit_path: Path) -> None:
        """No global writer started → AuditLogger sync-writes via
        ChainedJsonlWriter directly (= pre-C22 behaviour)."""
        from openakita.core.audit_logger import AuditLogger

        # Ensure no singleton up
        reset_for_testing()
        audit = AuditLogger(path=str(audit_path), enabled=True, include_chain=True)
        audit.log(
            tool_name="run_shell",
            decision="allow",
            reason="sync path",
            params_preview="echo hello",
        )
        audit.log(
            tool_name="run_shell",
            decision="deny",
            reason="sync path",
            params_preview="rm -rf /",
        )
        result = verify_chain(audit_path)
        assert result.ok
        assert result.total == 2


# ---------------------------------------------------------------------------
# 8. Global writer singleton
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    @pytest.mark.asyncio
    async def test_start_replaces_old_writer_on_path_change(self, tmp_path: Path) -> None:
        path_a = tmp_path / "a.jsonl"
        path_b = tmp_path / "b.jsonl"
        w1 = await start_global_audit_writer(str(path_a))
        w2 = await start_global_audit_writer(str(path_b))
        assert w1 is not w2
        assert not w1.is_running()
        assert w2.is_running()
        await stop_global_audit_writer()

    @pytest.mark.asyncio
    async def test_start_is_idempotent_for_same_path(self, audit_path: Path) -> None:
        w1 = await start_global_audit_writer(str(audit_path))
        w2 = await start_global_audit_writer(str(audit_path))
        assert w1 is w2  # idempotent
        await stop_global_audit_writer()

    @pytest.mark.asyncio
    async def test_get_returns_none_when_path_mismatch(self, tmp_path: Path) -> None:
        """If singleton is for path A but caller asks for path B, return
        None so caller falls back to sync (don't accidentally write to
        the wrong file)."""
        path_a = tmp_path / "a.jsonl"
        path_b = tmp_path / "b.jsonl"
        await start_global_audit_writer(str(path_a))
        try:
            w = aw_mod.get_async_audit_writer(str(path_b))
            assert w is None
        finally:
            await stop_global_audit_writer()


# ---------------------------------------------------------------------------
# 9. Module surface sanity
# ---------------------------------------------------------------------------


def test_module_exports_match_all() -> None:
    """Sanity: __all__ contains the public surface we promise."""
    assert "AsyncBatchAuditWriter" in aw_mod.__all__
    assert "start_global_audit_writer" in aw_mod.__all__
    assert "stop_global_audit_writer" in aw_mod.__all__
    assert "get_async_audit_writer" in aw_mod.__all__


def test_append_batch_exported_from_audit_chain() -> None:
    """Public ChainedJsonlWriter must expose append_batch."""
    assert hasattr(audit_chain.ChainedJsonlWriter, "append_batch")
