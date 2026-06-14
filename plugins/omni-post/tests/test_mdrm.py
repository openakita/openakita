"""Tests for the omni-post MDRM adapter.

The adapter must be robust against three common failure modes:

* host API entirely absent (``api=None`` in tests);
* the API object exists but ``get_memory_manager()`` raises;
* ``memory.add_memory`` raises after we call it.

None of these may propagate — the publish pipeline should always be
able to ``await adapter.write_publish_memory(...)`` safely.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from omni_post_mdrm import OmniPostMdrmAdapter, PublishMemoryRecord


# ---------------------------------------------------------------------------
# PublishMemoryRecord
# ---------------------------------------------------------------------------


def _record(success: bool = True, error: str | None = None) -> PublishMemoryRecord:
    return PublishMemoryRecord(
        task_id="tk-1",
        platform="douyin",
        account_id="acc-7",
        success=success,
        ts_utc=datetime(2026, 4, 24, 21, 15, tzinfo=timezone.utc),
        engine="pw",
        error_kind=error,
        asset_kind="video",
        duration_ms=12_345,
        published_url="https://douyin.com/video/99" if success else None,
    )


def test_record_tags_include_platform_and_time_buckets() -> None:
    r = _record()
    tags = r.tags()
    assert "omni-post" in tags
    assert "platform:douyin" in tags
    assert "account:acc-7" in tags
    assert "engine:pw" in tags
    assert "hour:21" in tags
    # 2026-04-24 is a Friday (weekday=4 in Python's ISO calendar)
    assert f"weekday:{r.weekday}" in tags
    assert "outcome:success" in tags


def test_failure_record_embeds_error_tag_and_predicate() -> None:
    r = _record(success=False, error="cookie_expired")
    assert r.predicate() == "failure:cookie_expired"
    assert "outcome:failure" in r.tags()
    assert "error:cookie_expired" in r.tags()


def test_content_mentions_engine_url_and_error() -> None:
    ok = _record().content()
    bad = _record(success=False, error="network").content()
    assert "published to douyin" in ok
    assert "douyin.com" in ok
    assert "failed to publish" in bad
    assert "(network)" in bad


# ---------------------------------------------------------------------------
# Adapter — degraded paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_skips_when_api_is_none() -> None:
    ad = OmniPostMdrmAdapter(api=None)
    result = await ad.write_publish_memory(_record())
    assert result["status"] == "skipped"
    assert ad.caps.has_memory_write is False


class _ExplosiveAPI:
    def get_memory_manager(self) -> Any:
        raise RuntimeError("perm denied")

    def get_brain(self) -> Any:
        return None

    def get_vector_store(self) -> Any:
        return None


@pytest.mark.asyncio
async def test_adapter_tolerates_raising_host() -> None:
    ad = OmniPostMdrmAdapter(api=_ExplosiveAPI())
    result = await ad.write_publish_memory(_record())
    assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# Adapter — happy path
# ---------------------------------------------------------------------------


class _CapturingMemory:
    def __init__(self) -> None:
        self.adds: list[tuple[Any, str, str]] = []

    def add_memory(
        self, memory: Any, scope: str = "global", scope_owner: str = ""
    ) -> str:
        self.adds.append((memory, scope, scope_owner))
        return "mem-42"


class _API:
    def __init__(self, memory: Any) -> None:
        self._m = memory

    def get_memory_manager(self) -> Any:
        return self._m

    def get_brain(self) -> Any:
        return None

    def get_vector_store(self) -> Any:
        return None


@pytest.mark.asyncio
async def test_adapter_writes_full_memory_object() -> None:
    mem = _CapturingMemory()
    ad = OmniPostMdrmAdapter(api=_API(mem))
    assert ad.caps.has_memory_write is True

    result = await ad.write_publish_memory(_record())
    assert result == {"status": "ok", "memory_id": "mem-42"}
    assert len(mem.adds) == 1
    memory_obj, scope, owner = mem.adds[0]
    assert scope == "global"
    assert owner == "omni-post"

    tags = getattr(memory_obj, "tags", None) or memory_obj["tags"]
    assert "platform:douyin" in tags
    assert "outcome:success" in tags


class _BadMemory:
    def add_memory(
        self, memory: Any, scope: str = "global", scope_owner: str = ""
    ) -> str:
        raise ValueError("db locked")


@pytest.mark.asyncio
async def test_adapter_wraps_add_memory_exception() -> None:
    ad = OmniPostMdrmAdapter(api=_API(_BadMemory()))
    result = await ad.write_publish_memory(_record())
    assert result["status"] == "error"
    assert result["reason"] == "ValueError"


class _PositionalOnlyMemory:
    """Some hosts in the wild only accept ``add_memory(obj)``."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def add_memory(self, memory: Any) -> str:
        self.calls.append(memory)
        return "mem-legacy"


@pytest.mark.asyncio
async def test_adapter_falls_back_to_single_arg_signature() -> None:
    mem = _PositionalOnlyMemory()
    ad = OmniPostMdrmAdapter(api=_API(mem))
    result = await ad.write_publish_memory(_record())
    assert result == {"status": "ok", "memory_id": "mem-legacy"}
    assert len(mem.calls) == 1
