"""Tests for clip_task_manager.py — CRUD, whitelist, transcripts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest
from clip_task_manager import DEFAULT_CONFIG, TaskManager


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_clip_sense.db"


@pytest.fixture()
def tm(db_path: Path) -> TaskManager:
    return TaskManager(db_path)


def run(coro):
    """Helper to run async code in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestConfig:
    def test_default_config_seeded(self, tm: TaskManager):
        run(tm.init())
        try:
            cfg = run(tm.get_all_config())
            for key in DEFAULT_CONFIG:
                assert key in cfg
                assert cfg[key] == DEFAULT_CONFIG[key]
        finally:
            run(tm.close())

    def test_set_and_get_config(self, tm: TaskManager):
        run(tm.init())
        try:
            run(tm.set_config("dashscope_api_key", "sk-test123"))
            val = run(tm.get_config("dashscope_api_key"))
            assert val == "sk-test123"
        finally:
            run(tm.close())

    def test_set_configs_batch(self, tm: TaskManager):
        run(tm.init())
        try:
            run(tm.set_configs({"dashscope_api_key": "k1", "ffmpeg_path": "/usr/bin/ffmpeg"}))
            assert run(tm.get_config("dashscope_api_key")) == "k1"
            assert run(tm.get_config("ffmpeg_path")) == "/usr/bin/ffmpeg"
        finally:
            run(tm.close())

    def test_get_config_missing(self, tm: TaskManager):
        run(tm.init())
        try:
            assert run(tm.get_config("nonexistent_key")) is None
        finally:
            run(tm.close())


class TestTaskCRUD:
    def test_create_and_get(self, tm: TaskManager):
        run(tm.init())
        try:
            task = run(tm.create_task(mode="silence_clean", source_video_path="/tmp/v.mp4"))
            assert task["mode"] == "silence_clean"
            assert task["status"] == "pending"
            assert task["source_video_path"] == "/tmp/v.mp4"
            assert len(task["id"]) == 12

            fetched = run(tm.get_task(task["id"]))
            assert fetched is not None
            assert fetched["id"] == task["id"]
        finally:
            run(tm.close())

    def test_update_task(self, tm: TaskManager):
        run(tm.init())
        try:
            task = run(tm.create_task(mode="highlight_extract"))
            run(tm.update_task(task["id"], status="running", pipeline_step="transcribe"))
            updated = run(tm.get_task(task["id"]))
            assert updated is not None
            assert updated["status"] == "running"
            assert updated["pipeline_step"] == "transcribe"
        finally:
            run(tm.close())

    def test_update_task_json_fields(self, tm: TaskManager):
        run(tm.init())
        try:
            task = run(tm.create_task(mode="highlight_extract"))
            segments = [{"start": 10.5, "end": 25.0, "reason": "funny"}]
            run(tm.update_task(task["id"], segments=segments))
            updated = run(tm.get_task(task["id"]))
            assert updated is not None
            assert updated["segments"] == segments
        finally:
            run(tm.close())

    def test_update_task_whitelist_guard(self, tm: TaskManager):
        run(tm.init())
        try:
            task = run(tm.create_task(mode="silence_clean"))
            with pytest.raises(ValueError, match="not whitelisted"):
                run(tm.update_task(task["id"], id="hacked"))
        finally:
            run(tm.close())

    def test_update_task_whitelist_guard_created_at(self, tm: TaskManager):
        run(tm.init())
        try:
            task = run(tm.create_task(mode="silence_clean"))
            with pytest.raises(ValueError, match="not whitelisted"):
                run(tm.update_task(task["id"], created_at="2000-01-01"))
        finally:
            run(tm.close())

    def test_delete_task(self, tm: TaskManager):
        run(tm.init())
        try:
            task = run(tm.create_task(mode="topic_split"))
            assert run(tm.delete_task(task["id"])) is True
            assert run(tm.get_task(task["id"])) is None
            assert run(tm.delete_task(task["id"])) is False
        finally:
            run(tm.close())

    def test_list_tasks(self, tm: TaskManager):
        run(tm.init())
        try:
            run(tm.create_task(mode="silence_clean"))
            run(tm.create_task(mode="highlight_extract"))
            run(tm.create_task(mode="silence_clean"))

            result = run(tm.list_tasks())
            assert result["total"] == 3
            assert len(result["tasks"]) == 3

            result = run(tm.list_tasks(mode="silence_clean"))
            assert result["total"] == 2

            result = run(tm.list_tasks(status="pending"))
            assert result["total"] == 3
        finally:
            run(tm.close())

    def test_list_tasks_pagination(self, tm: TaskManager):
        run(tm.init())
        try:
            for _i in range(5):
                run(tm.create_task(mode="silence_clean"))
            result = run(tm.list_tasks(limit=2, offset=0))
            assert len(result["tasks"]) == 2
            assert result["total"] == 5
        finally:
            run(tm.close())

    def test_get_running_tasks(self, tm: TaskManager):
        run(tm.init())
        try:
            t1 = run(tm.create_task(mode="silence_clean"))
            t2 = run(tm.create_task(mode="highlight_extract"))
            run(tm.update_task(t1["id"], status="running"))
            run(tm.update_task(t2["id"], status="succeeded"))

            running = run(tm.get_running_tasks())
            assert len(running) == 1
            assert running[0]["status"] == "running"
        finally:
            run(tm.close())

    def test_get_task_nonexistent(self, tm: TaskManager):
        run(tm.init())
        try:
            assert run(tm.get_task("nonexistent")) is None
        finally:
            run(tm.close())


class TestTranscriptCRUD:
    def test_create_and_get(self, tm: TaskManager):
        run(tm.init())
        try:
            t = run(tm.create_transcript(
                source_hash="abc123def456",
                source_path="/tmp/v.mp4",
                source_name="v.mp4",
                duration_sec=120.5,
            ))
            assert t["source_hash"] == "abc123def456"
            assert t["status"] == "pending"

            fetched = run(tm.get_transcript(t["id"]))
            assert fetched is not None
            assert fetched["source_name"] == "v.mp4"
        finally:
            run(tm.close())

    def test_get_by_hash(self, tm: TaskManager):
        run(tm.init())
        try:
            t = run(tm.create_transcript(source_hash="hash1234"))
            found = run(tm.get_transcript_by_hash("hash1234"))
            assert found is not None
            assert found["id"] == t["id"]

            assert run(tm.get_transcript_by_hash("nonexistent")) is None
        finally:
            run(tm.close())

    def test_update_transcript(self, tm: TaskManager):
        run(tm.init())
        try:
            t = run(tm.create_transcript(source_hash="h1"))
            sentences = [{"text": "hello", "start": 0.0, "end": 1.5}]
            run(tm.update_transcript(t["id"], status="succeeded", sentences=sentences, full_text="hello"))
            updated = run(tm.get_transcript(t["id"]))
            assert updated is not None
            assert updated["status"] == "succeeded"
            assert updated["sentences"] == sentences
            assert updated["full_text"] == "hello"
        finally:
            run(tm.close())

    def test_update_transcript_whitelist_guard(self, tm: TaskManager):
        run(tm.init())
        try:
            t = run(tm.create_transcript(source_hash="h2"))
            with pytest.raises(ValueError, match="not whitelisted"):
                run(tm.update_transcript(t["id"], id="hacked"))
        finally:
            run(tm.close())

    def test_delete_transcript(self, tm: TaskManager):
        run(tm.init())
        try:
            t = run(tm.create_transcript(source_hash="h3"))
            assert run(tm.delete_transcript(t["id"])) is True
            assert run(tm.get_transcript(t["id"])) is None
        finally:
            run(tm.close())

    def test_list_transcripts(self, tm: TaskManager):
        run(tm.init())
        try:
            run(tm.create_transcript(source_hash="a"))
            run(tm.create_transcript(source_hash="b"))
            result = run(tm.list_transcripts())
            assert result["total"] == 2
            assert len(result["transcripts"]) == 2
        finally:
            run(tm.close())

    def test_hash_uniqueness(self, tm: TaskManager):
        run(tm.init())
        try:
            run(tm.create_transcript(source_hash="dup1"))
            with pytest.raises(aiosqlite.IntegrityError):
                run(tm.create_transcript(source_hash="dup1"))
        finally:
            run(tm.close())

    def test_get_or_create_reuses_row_after_failed(self, tm: TaskManager):
        run(tm.init())
        try:
            first = run(
                tm.create_transcript(
                    source_hash="retry_hash",
                    source_path="/old/path.mp4",
                    source_name="old.mp4",
                    duration_sec=10.0,
                ),
            )
            run(
                tm.update_transcript(
                    first["id"],
                    status="failed",
                    error_message="Paraformer FAILED",
                ),
            )
            second = run(
                tm.get_or_create_transcript_for_hash(
                    source_hash="retry_hash",
                    source_path="/new/path.mp4",
                    source_name="new.mp4",
                    duration_sec=11.0,
                ),
            )
            assert second["id"] == first["id"]
            assert second["status"] == "pending"
            assert second["source_path"] == "/new/path.mp4"
            assert second["source_name"] == "new.mp4"
            assert second["duration_sec"] == 11.0
            assert second.get("error_message") in (None, "")
            rows = run(tm.list_transcripts())
            assert rows["total"] == 1
        finally:
            run(tm.close())
