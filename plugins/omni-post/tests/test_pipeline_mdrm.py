"""Integration-ish test: pipeline ↔ MDRM adapter wiring.

We don't spin Playwright — we go straight at ``_write_publish_memory``
through a synthetic ``PipelineDeps`` with an ``OmniPostMdrmAdapter``
hooked up to a capturing fake memory manager.

The promise under test: **every** terminal write through the pipeline
leaves exactly one memory record with the right platform / account /
outcome tags, and a raising memory manager never crashes the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from omni_post_mdrm import OmniPostMdrmAdapter
from omni_post_pipeline import PipelineDeps, _write_publish_memory


class _FakeMemory:
    def __init__(self) -> None:
        self.writes: list[Any] = []

    def add_memory(
        self, memory: Any, scope: str = "global", scope_owner: str = ""
    ) -> str:
        self.writes.append(memory)
        return f"mem-{len(self.writes)}"


class _FakeAPI:
    def __init__(self, memory: Any) -> None:
        self._m = memory

    def get_memory_manager(self) -> Any:
        return self._m

    def get_brain(self) -> Any:
        return None

    def get_vector_store(self) -> Any:
        return None


def _make_deps(api: Any) -> PipelineDeps:
    adapter = OmniPostMdrmAdapter(api=api)
    # PipelineDeps insists on a few required fields; we feed stubs.
    return PipelineDeps(
        task_manager=None,  # type: ignore[arg-type]
        cookie_pool=None,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        selectors_dir=None,  # type: ignore[arg-type]
        screenshot_dir=None,  # type: ignore[arg-type]
        settings={},
        api=api,
        mdrm=adapter,
    )


def _task(platform: str = "douyin", account: str = "acc-7") -> dict[str, Any]:
    return {
        "id": "tk-1",
        "platform": platform,
        "account_id": account,
        "engine": "pw",
        "started_at": datetime(2026, 4, 24, 21, 14, tzinfo=timezone.utc).isoformat(),
    }


class _Outcome:
    def __init__(self) -> None:
        self.published_url = "https://douyin.com/video/77"


@pytest.mark.asyncio
async def test_pipeline_writes_memory_on_success() -> None:
    memory = _FakeMemory()
    deps = _make_deps(_FakeAPI(memory))
    await _write_publish_memory(
        deps,
        _task(),
        _Outcome(),
        {"id": "ast-1", "kind": "video"},
        success=True,
    )
    assert len(memory.writes) == 1
    tags = memory.writes[0].tags
    assert "platform:douyin" in tags
    assert "account:acc-7" in tags
    assert "engine:pw" in tags
    assert "outcome:success" in tags
    assert "asset:video" in tags


@pytest.mark.asyncio
async def test_pipeline_writes_memory_on_failure() -> None:
    memory = _FakeMemory()
    deps = _make_deps(_FakeAPI(memory))
    await _write_publish_memory(
        deps,
        _task(platform="rednote", account="acc-9"),
        None,
        None,
        success=False,
        error="cookie_expired",
    )
    assert len(memory.writes) == 1
    tags = memory.writes[0].tags
    assert "platform:rednote" in tags
    assert "account:acc-9" in tags
    assert "outcome:failure" in tags
    assert "error:cookie_expired" in tags


class _ExplosiveMemory:
    def add_memory(self, *_a: Any, **_kw: Any) -> str:
        raise RuntimeError("db corrupted")


@pytest.mark.asyncio
async def test_pipeline_survives_raising_memory() -> None:
    deps = _make_deps(_FakeAPI(_ExplosiveMemory()))
    # Must NOT raise — the pipeline promise.
    await _write_publish_memory(deps, _task(), _Outcome(), None, success=True)


@pytest.mark.asyncio
async def test_pipeline_no_mdrm_no_throw() -> None:
    deps = _make_deps(None)
    # Adapter with api=None degrades cleanly.
    await _write_publish_memory(deps, _task(), _Outcome(), None, success=True)
