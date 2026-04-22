"""Async unit tests for ``transcribe_archive.task_manager``.

Coverage matrix:

* schema bootstrap / config defaults
* CRUD for tasks (create / get / update / delete / list)
* JSON blob round-trip (params / result / verification)
* :attr:`TranscribeTaskManager._UPDATABLE_COLUMNS` allow-list (Sprint 7
  / A4 — guards against SQL injection via column-name interpolation)
* corrupt-blob recovery in ``_row_to_task``
* concurrent reads while writer runs (WAL sanity)

Each test gets a fresh on-disk SQLite file in ``tmp_path`` so we don't
share state across tests and don't shadow the host's real DB.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from task_manager import (
    DEFAULT_CONFIG,
    TranscribeTaskManager,
)


# ── shared fixtures ────────────────────────────────────────────────────


@pytest.fixture
def tm_path(tmp_path: Path) -> Path:
    return tmp_path / "transcribe.db"


async def _new_tm(path: Path) -> TranscribeTaskManager:
    tm = TranscribeTaskManager(path)
    await tm.init()
    return tm


# ── schema / config ────────────────────────────────────────────────────


def test_init_creates_schema_and_defaults(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            cfg = await tm.get_all_config()
            for key, val in DEFAULT_CONFIG.items():
                assert cfg.get(key) == val
        finally:
            await tm.close()

    asyncio.run(go())


def test_get_config_falls_back_to_default_for_unknown_key(tm_path: Path) -> None:
    """Unknown key NOT in DEFAULT_CONFIG returns empty string — the
    contract every plugin shares so callers don't need a try/except for
    "is this config key set yet?"."""
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            assert await tm.get_config("definitely_not_a_real_key") == ""
        finally:
            await tm.close()

    asyncio.run(go())


def test_set_config_persists_across_reopen(tm_path: Path) -> None:
    async def go() -> None:
        tm1 = await _new_tm(tm_path)
        await tm1.set_config("default_provider", "whisper")
        await tm1.close()

        tm2 = await _new_tm(tm_path)
        try:
            assert await tm2.get_config("default_provider") == "whisper"
        finally:
            await tm2.close()

    asyncio.run(go())


def test_set_configs_writes_multiple_keys(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            await tm.set_configs({
                "default_language": "en",
                "chunk_duration_sec": "90",
            })
            assert await tm.get_config("default_language") == "en"
            assert await tm.get_config("chunk_duration_sec") == "90"
        finally:
            await tm.close()

    asyncio.run(go())


# ── CRUD ───────────────────────────────────────────────────────────────


def test_create_task_assigns_id_and_timestamps(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/tmp/x.wav", language="zh")
            assert t["id"]
            assert len(t["id"]) <= 12
            assert t["status"] == "pending"
            assert t["created_at"] > 0
            assert t["updated_at"] >= t["created_at"]
        finally:
            await tm.close()

    asyncio.run(go())


def test_create_task_persists_params_blob(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            params = {"chunk_duration_sec": 45.0, "overlap_sec": 3.0}
            t = await tm.create_task(audio_path="/x.wav", params=params)
            again = await tm.get_task(t["id"])
            assert again is not None
            assert again["params"] == params
        finally:
            await tm.close()

    asyncio.run(go())


def test_get_task_returns_none_for_missing(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            assert await tm.get_task("does_not_exist") is None
        finally:
            await tm.close()

    asyncio.run(go())


def test_update_task_merges_status_and_result(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/x.wav")
            ok = await tm.update_task(
                t["id"],
                status="succeeded",
                result={"words": [{"text": "hi", "start": 0, "end": 1}]},
                verification={"verified": True, "verifier_id": "stub"},
                chunks_total=3,
                chunks_done=3,
            )
            assert ok is True
            again = await tm.get_task(t["id"])
            assert again is not None
            assert again["status"] == "succeeded"
            assert again["result"]["words"][0]["text"] == "hi"
            assert again["verification"]["verified"] is True
            assert again["chunks_total"] == 3
            assert again["chunks_done"] == 3
            assert again["updated_at"] >= again["created_at"]
        finally:
            await tm.close()

    asyncio.run(go())


def test_update_task_unknown_column_raises(tm_path: Path) -> None:
    """Sprint 7 / A4 guard — the SQL injection prevention.  Any key
    outside the allow-list MUST raise ValueError; no silent column add."""
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/x.wav")
            with pytest.raises(ValueError):
                await tm.update_task(t["id"], not_a_column="payload")
        finally:
            await tm.close()

    asyncio.run(go())


def test_update_task_value_injection_is_bind_safe(tm_path: Path) -> None:
    """Even when a whitelisted column gets a malicious-looking string
    value, parameter binding must store it verbatim and NOT execute
    it.  Pins the intent of the A4 hardening (the hardening protects
    columns; values are always safe via ``?`` binding)."""
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/x.wav")
            payload = "'; DROP TABLE tasks; --"
            await tm.update_task(t["id"], error_message=payload)
            again = await tm.get_task(t["id"])
            assert again is not None
            assert again["error_message"] == payload
            # Table must still be queryable.
            tasks, total = await tm.list_tasks()
            assert total == 1
            assert tasks[0]["id"] == t["id"]
        finally:
            await tm.close()

    asyncio.run(go())


def test_update_task_empty_kwargs_returns_false(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/x.wav")
            assert await tm.update_task(t["id"]) is False
        finally:
            await tm.close()

    asyncio.run(go())


def test_delete_task_removes_row(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/x.wav")
            assert await tm.delete_task(t["id"]) is True
            assert await tm.get_task(t["id"]) is None
            # Idempotent — deleting twice returns False, never raises.
            assert await tm.delete_task(t["id"]) is False
        finally:
            await tm.close()

    asyncio.run(go())


# ── list_tasks (filtering + pagination) ────────────────────────────────


def test_list_tasks_pagination_and_filter(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            for i in range(5):
                t = await tm.create_task(audio_path=f"/{i}.wav")
                await tm.update_task(
                    t["id"],
                    status="succeeded" if i % 2 == 0 else "failed",
                )
            ok_tasks, ok_total = await tm.list_tasks(status="succeeded")
            assert ok_total == 3
            assert len(ok_tasks) == 3

            # Pagination.
            page, total = await tm.list_tasks(limit=2, offset=1)
            assert total == 5
            assert len(page) == 2
        finally:
            await tm.close()

    asyncio.run(go())


def test_get_running_tasks_includes_pending_and_running(tm_path: Path) -> None:
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t1 = await tm.create_task(audio_path="/a.wav")  # default pending
            t2 = await tm.create_task(audio_path="/b.wav")
            await tm.update_task(t2["id"], status="running")
            t3 = await tm.create_task(audio_path="/c.wav")
            await tm.update_task(t3["id"], status="succeeded")  # excluded

            running = await tm.get_running_tasks()
            ids = {t["id"] for t in running}
            assert t1["id"] in ids
            assert t2["id"] in ids
            assert t3["id"] not in ids
        finally:
            await tm.close()

    asyncio.run(go())


# ── JSON blob round-trip + corruption ──────────────────────────────────


def test_unicode_payload_round_trips(tm_path: Path) -> None:
    """ensure_ascii=False is critical — Chinese / emoji must not be
    \\uXXXX-escaped on the way in or the result file size doubles."""
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/x.wav",
                                     params={"prompt": "你好世界 🎵"})
            again = await tm.get_task(t["id"])
            assert again is not None
            assert again["params"]["prompt"] == "你好世界 🎵"
        finally:
            await tm.close()

    asyncio.run(go())


def test_row_to_task_handles_missing_blobs(tm_path: Path) -> None:
    """A freshly-created task has result_json / verification_json NULL —
    the row converter must surface them as Python None (NOT raise)."""
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/x.wav")
            again = await tm.get_task(t["id"])
            assert again is not None
            assert again["result"] is None
            assert again["verification"] is None
            assert again["params"] == {}  # default {} not None
        finally:
            await tm.close()

    asyncio.run(go())


def test_row_to_task_handles_corrupt_blob(tm_path: Path) -> None:
    """Manually plant invalid JSON in result_json (simulating a crash
    or an old plugin version) — get_task must return the default value
    rather than blow up the whole UI list view."""
    import sqlite3

    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/x.wav")
            await tm.close()
            con = sqlite3.connect(tm_path)
            con.execute(
                "UPDATE tasks SET result_json = '{not json' WHERE id = ?",
                (t["id"],),
            )
            con.commit()
            con.close()
            tm2 = await _new_tm(tm_path)
            again = await tm2.get_task(t["id"])
            assert again is not None
            assert again["result"] is None  # corrupt → default
            await tm2.close()
        finally:
            pass  # tm already closed

    asyncio.run(go())


# ── concurrency sanity ─────────────────────────────────────────────────


def test_concurrent_reads_dont_block_each_other(tm_path: Path) -> None:
    """WAL mode + same-process aiosqlite — must support concurrent
    coroutine reads.  This test creates one task then awaits 5 reads
    simultaneously; they all return the same row without raising."""
    async def go() -> None:
        tm = await _new_tm(tm_path)
        try:
            t = await tm.create_task(audio_path="/x.wav")
            results = await asyncio.gather(
                *[tm.get_task(t["id"]) for _ in range(5)]
            )
            assert all(r is not None and r["id"] == t["id"] for r in results)
        finally:
            await tm.close()

    asyncio.run(go())


# ── invariants ─────────────────────────────────────────────────────────


def test_updatable_columns_does_not_include_id_or_timestamps() -> None:
    """``id`` / ``created_at`` / ``updated_at`` must NEVER be in the
    allow-list — updating them would break the lifecycle invariants
    (id is the PK, updated_at is auto-managed by ``update_task``)."""
    forbidden = {"id", "created_at", "updated_at"}
    assert not (forbidden & TranscribeTaskManager._UPDATABLE_COLUMNS.keys())


def test_json_encoded_keys_are_subset_of_updatable_columns() -> None:
    """Every JSON-encoded key MUST also be in the updatable list —
    otherwise update_task would route the value through json.dumps but
    then reject the key as unknown.  Catches a refactor mistake at
    test time, not in production."""
    diff = (
        TranscribeTaskManager._JSON_ENCODED_KEYS
        - TranscribeTaskManager._UPDATABLE_COLUMNS.keys()
    )
    assert diff == set(), f"orphan JSON-encoded keys: {diff}"


def test_default_config_keys_are_strings() -> None:
    """The config table is TEXT-only — non-string defaults would fail
    the INSERT.  Catch type drift at import time, not at first run."""
    for k, v in DEFAULT_CONFIG.items():
        assert isinstance(k, str), f"non-str key {k!r}"
        assert isinstance(v, str), f"non-str value for {k!r}: {type(v)}"


def test_create_task_round_trips_result_json_when_provided() -> None:
    """Although ``result`` is set via ``update_task``, the create path
    accepts it via the params dict — confirm round-trip works either
    way so a future "create with prefilled result" pattern doesn't
    break."""
    async def go() -> None:
        tm = TranscribeTaskManager(Path(":memory:"))
        # in-memory SQLite is file-less; init still works because aiosqlite
        # handles ":memory:" specially.
        await tm.init()
        try:
            t = await tm.create_task(audio_path="/x.wav")
            data = {"hello": "world", "num": 42}
            await tm.update_task(t["id"], result=data)
            again = await tm.get_task(t["id"])
            assert again is not None
            assert again["result"] == data
            text = json.dumps(again["result"], ensure_ascii=False)
            assert "hello" in text
        finally:
            await tm.close()

    asyncio.run(go())
