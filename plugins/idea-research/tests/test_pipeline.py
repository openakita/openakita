"""Unit tests for ``idea_pipeline``.

Mocks every external dep (DashScope client, collector registry, MDRM
adapter, subprocess-based media steps) so we can drive each step
deterministically and verify:

* All 4 mode happy paths run end-to-end and produce the expected
  ``output_json`` / ``cost_cny`` shape.
* Failure injection (network, dependency, format, rate_limit) routes
  through :func:`_record_failure` with the right ``error_kind``.
* Optional steps degrade gracefully (ASR / VLM / comments / MDRM) —
  the task still reaches ``status='done'`` when only optional bits
  fail.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import idea_pipeline as pipeline
import pytest
from idea_dashscope_client import (
    ChatResult,
    FrameDescription,
    TranscriptResult,
    TranscriptSegment,
)
from idea_models import TrendItem
from idea_research_inline.mdrm_adapter import HookRecord
from idea_research_inline.vendor_client import (
    VendorError,
    VendorNetworkError,
    VendorRateLimitError,
)
from idea_task_manager import IdeaTaskManager

# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class FakeDashScope:
    chat_responses: list[ChatResult] = field(default_factory=list)
    chat_calls: list[dict[str, Any]] = field(default_factory=list)
    describe_responses: list[FrameDescription] = field(default_factory=list)
    describe_calls: list[Path] = field(default_factory=list)
    transcript_response: TranscriptResult | None = None
    transcript_error: Exception | None = None
    chat_error: Exception | None = None

    async def chat_completion(self, **kwargs: Any) -> ChatResult:
        self.chat_calls.append(kwargs)
        if self.chat_error is not None:
            raise self.chat_error
        if not self.chat_responses:
            return ChatResult(content="{}", model=kwargs.get("model", "qwen-max"), parsed_json={})
        return self.chat_responses.pop(0)

    async def describe_image(self, path: Path, **_: Any) -> FrameDescription:
        self.describe_calls.append(path)
        if self.describe_responses:
            return self.describe_responses.pop(0)
        return FrameDescription(desc=f"frame:{Path(path).name}")

    async def transcribe_audio(self, audio_path: Path, **_: Any) -> TranscriptResult:
        if self.transcript_error is not None:
            raise self.transcript_error
        return self.transcript_response or TranscriptResult(
            backend="local",
            text="hello world",
            segments=[TranscriptSegment(0.0, 1.0, "hello"), TranscriptSegment(1.0, 2.0, "world")],
            language="zh",
        )


@dataclass
class FakeRegistry:
    items: list[TrendItem] = field(default_factory=list)
    fetch_url_response: TrendItem | None = None
    fetch_url_error: Exception | None = None
    fetch_radar_error: Exception | None = None
    radar_choices: list[dict[str, Any]] = field(default_factory=list)

    async def fetch_single_url(self, url: str, *, with_comments: bool = False) -> TrendItem | None:
        if self.fetch_url_error is not None:
            raise self.fetch_url_error
        if self.fetch_url_response is not None:
            return self.fetch_url_response
        return TrendItem(
            id="ti-1",
            platform="bilibili",
            external_id="BV1xx",
            external_url=url,
            title="Sample title",
            author="creator",
            duration_seconds=88,
            publish_at=int(time.time()) - 3600,
            fetched_at=int(time.time()),
        )

    async def fetch_for_radar(
        self, platforms: list[str], keywords: list[str], **kwargs: Any
    ) -> dict[str, Any]:
        if self.fetch_radar_error is not None:
            raise self.fetch_radar_error
        return {
            "items": list(self.items),
            "errors": [],
            "choices": list(self.radar_choices),
            "fetched_at": int(time.time()),
        }


class FakeMdrm:
    def __init__(
        self,
        *,
        write_result: dict[str, str] | None = None,
        search_results: list[tuple[Any, float]] | None = None,
        write_error: Exception | None = None,
    ) -> None:
        self.write_calls: list[HookRecord] = []
        self.search_calls: list[str] = []
        self._write_result = write_result or {"vector": "ok", "memory": "ok"}
        self._search_results = search_results or []
        self._write_error = write_error

    async def write_hook(self, hook: HookRecord) -> dict[str, str]:
        self.write_calls.append(hook)
        if self._write_error is not None:
            raise self._write_error
        return self._write_result

    async def search_similar_hooks(
        self, query_text: str, *, limit: int = 5, **kwargs: Any
    ) -> list[tuple[Any, float]]:
        self.search_calls.append(query_text)
        return self._search_results


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture()
async def task_manager(tmp_path: Path):  # type: ignore[no-untyped-def]
    tm = IdeaTaskManager(db_path=tmp_path / "idea.sqlite")
    await tm.init()
    try:
        yield tm
    finally:
        await tm.close()


def _structure_payload() -> dict[str, Any]:
    return {
        "hook": {"type": "悬念", "text": "你不会相信", "time_range": [0, 5]},
        "body": [{"topic": "讲故事", "time_range": [5, 30], "key_quote": "啊"}],
        "cta": {"text": "三连", "time_range": [55, 60]},
        "keywords": [{"word": "干货", "freq": 3, "weight": 0.9}],
        "estimated_quality": 0.78,
    }


async def _seed_breakdown_task(
    tm: IdeaTaskManager,
    *,
    mode: str = "breakdown_url",
    inp: dict[str, Any] | None = None,
) -> str:
    return await tm.insert_task(mode=mode, input_payload=inp or {})


def _build_ctx(
    *,
    tm: IdeaTaskManager,
    task_id: str,
    work_dir: Path,
    mode: str = "breakdown_url",
    inp: dict[str, Any] | None = None,
    persona: str | None = "情感共鸣型",
    dashscope: FakeDashScope | None = None,
    registry: FakeRegistry | None = None,
    mdrm: FakeMdrm | None = None,
) -> pipeline.IdeaPipelineContext:
    return pipeline.IdeaPipelineContext(
        task_id=task_id,
        mode=mode,
        input=inp or {},
        work_dir=work_dir,
        tm=tm,
        registry=registry or FakeRegistry(),
        dashscope=dashscope or FakeDashScope(),
        mdrm=mdrm or FakeMdrm(),
        persona_name=persona,
        download_media_fn=lambda wd, url: {
            "video": (wd / "video.mp4"),
            "audio": (wd / "audio.wav"),
        },
        extract_frames_fn=lambda video, out, strategy, max_frames: [
            (out / f"f{i}.jpg") for i in range(3)
        ],
    )


# --------------------------------------------------------------------------- #
# breakdown_url happy path                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_breakdown_url_happy_path(tmp_path: Path, task_manager: IdeaTaskManager) -> None:
    tm = task_manager
    inp = {"url": "https://www.bilibili.com/video/BV1xx", "write_to_mdrm": True}
    tid = await _seed_breakdown_task(tm, inp=inp)

    chat_results = [
        ChatResult(content="{}", model="qwen-max", parsed_json=_structure_payload()),
        ChatResult(
            content="{}",
            model="qwen-plus",
            parsed_json={"persona_takeaways": ["a", "b", "c", "d", "e"]},
        ),
    ]
    ds = FakeDashScope(chat_responses=chat_results)
    mdrm = FakeMdrm()
    ctx = _build_ctx(
        tm=tm,
        task_id=tid,
        work_dir=tmp_path / "wd",
        inp=inp,
        dashscope=ds,
        mdrm=mdrm,
    )

    out = await pipeline.run_breakdown_url(ctx)

    assert out["structure"]["hook"]["type"] == "悬念"
    assert out["persona_takeaways"] == ["a", "b", "c", "d", "e"]
    assert out["cost_cny"] > 0
    # work dir artefacts are written
    assert (ctx.work_dir / "breakdown.json").exists()
    assert (ctx.work_dir / "report.md").exists()
    # MDRM dual-track
    assert len(mdrm.write_calls) == 1
    hook = mdrm.write_calls[0]
    assert hook.hook_text == "你不会相信"
    # task row reflects done
    row = await tm.get_task(tid)
    assert row["status"] == "done"
    assert row["progress_pct"] == 100
    assert row["mdrm_writes_json"]
    payload = row["mdrm_writes_json"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["vector"] == "ok"


@pytest.mark.asyncio
async def test_breakdown_url_degrades_when_asr_fails(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    inp = {"url": "https://www.bilibili.com/video/BV1xx"}
    tid = await _seed_breakdown_task(tm, inp=inp)

    asr_err = VendorNetworkError("asr down")
    ds = FakeDashScope(
        chat_responses=[
            ChatResult(content="{}", model="qwen-max", parsed_json=_structure_payload()),
            ChatResult(
                content="{}",
                model="qwen-plus",
                parsed_json={"persona_takeaways": ["x"]},
            ),
        ],
        transcript_error=asr_err,
    )
    ctx = _build_ctx(tm=tm, task_id=tid, work_dir=tmp_path / "wd", inp=inp, dashscope=ds)
    out = await pipeline.run_breakdown_url(ctx)

    assert out["transcript"] is None
    assert out["structure"]["hook"]["type"] == "悬念"
    row = await tm.get_task(tid)
    assert row["status"] == "done"


# --------------------------------------------------------------------------- #
# breakdown_url failure injection                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_breakdown_url_missing_url_records_format_error(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    tid = await _seed_breakdown_task(tm, inp={})  # no url
    ctx = _build_ctx(tm=tm, task_id=tid, work_dir=tmp_path / "wd")
    with pytest.raises(VendorError):
        await pipeline.run_breakdown_url(ctx)
    row = await tm.get_task(tid)
    assert row["status"] == "failed"
    assert row["error_kind"] == "format"
    assert row["error_hint_zh"]
    assert row["error_hint_en"]


@pytest.mark.asyncio
async def test_breakdown_url_resolve_source_failure_records_network(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    inp = {"url": "https://x"}
    tid = await _seed_breakdown_task(tm, inp=inp)
    reg = FakeRegistry(fetch_url_error=VendorNetworkError("upstream down"))
    ctx = _build_ctx(tm=tm, task_id=tid, work_dir=tmp_path / "wd", inp=inp, registry=reg)
    with pytest.raises(VendorError):
        await pipeline.run_breakdown_url(ctx)
    row = await tm.get_task(tid)
    assert row["status"] == "failed"
    assert row["error_kind"] == "network"


@pytest.mark.asyncio
async def test_breakdown_url_structure_rate_limit_classified(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    inp = {"url": "https://www.bilibili.com/video/BV1xx"}
    tid = await _seed_breakdown_task(tm, inp=inp)
    ds = FakeDashScope(chat_error=VendorRateLimitError("slow down"))
    ctx = _build_ctx(tm=tm, task_id=tid, work_dir=tmp_path / "wd", inp=inp, dashscope=ds)
    with pytest.raises(VendorError):
        await pipeline.run_breakdown_url(ctx)
    row = await tm.get_task(tid)
    assert row["status"] == "failed"
    assert row["error_kind"] == "rate_limit"


@pytest.mark.asyncio
async def test_breakdown_url_dependency_error_when_download_missing_tools(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    inp = {"url": "https://www.bilibili.com/video/BV1xx"}
    tid = await _seed_breakdown_task(tm, inp=inp)

    def fail_download(wd: Path, url: str) -> dict[str, Path]:
        err = VendorError("yt-dlp not installed")
        err.error_kind = "dependency"
        raise err

    ctx = _build_ctx(tm=tm, task_id=tid, work_dir=tmp_path / "wd", inp=inp)
    ctx.download_media_fn = fail_download
    with pytest.raises(VendorError):
        await pipeline.run_breakdown_url(ctx)
    row = await tm.get_task(tid)
    assert row["error_kind"] == "dependency"
    assert "yt-dlp" in row["error_hint_zh"]


@pytest.mark.asyncio
async def test_breakdown_url_continues_when_mdrm_write_fails(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    inp = {"url": "https://www.bilibili.com/video/BV1xx"}
    tid = await _seed_breakdown_task(tm, inp=inp)
    chat_results = [
        ChatResult(content="{}", model="qwen-max", parsed_json=_structure_payload()),
        ChatResult(
            content="{}",
            model="qwen-plus",
            parsed_json={"persona_takeaways": ["a"]},
        ),
    ]
    mdrm = FakeMdrm(write_error=RuntimeError("vector store broken"))
    ds = FakeDashScope(chat_responses=chat_results)
    ctx = _build_ctx(tm=tm, task_id=tid, work_dir=tmp_path / "wd", inp=inp, dashscope=ds, mdrm=mdrm)
    out = await pipeline.run_breakdown_url(ctx)
    row = await tm.get_task(tid)
    assert row["status"] == "done"
    write_payload = row["mdrm_writes_json"]
    if isinstance(write_payload, str):
        write_payload = json.loads(write_payload)
    assert write_payload["vector"] == "error"
    assert "vector store broken" in write_payload["reason"]
    assert out["task_id"] == tid


# --------------------------------------------------------------------------- #
# radar_pull mode                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_radar_pull_persists_items(tmp_path: Path, task_manager: IdeaTaskManager) -> None:
    tm = task_manager
    tid = await _seed_breakdown_task(
        tm,
        mode="radar_pull",
        inp={"platforms": ["bilibili"], "keywords": ["AI"], "limit": 5},
    )
    items = [
        TrendItem(
            id=f"it-{i}",
            platform="bilibili",
            external_id=f"BV{i}",
            external_url=f"https://b/{i}",
            title=f"AI 视频 {i}",
            duration_seconds=60,
            like_count=100,
            view_count=1000,
            publish_at=int(time.time()) - 3600,
            fetched_at=int(time.time()),
            score=0.5 + i * 0.1,
        )
        for i in range(3)
    ]
    reg = FakeRegistry(items=items, radar_choices=[{"platform": "bilibili", "engine": "a"}])
    ctx = _build_ctx(
        tm=tm,
        task_id=tid,
        work_dir=tmp_path / "wd",
        mode="radar_pull",
        inp={"platforms": ["bilibili"], "keywords": ["AI"], "limit": 5},
        registry=reg,
    )
    out = await pipeline.run_radar_pull(ctx)
    assert sorted(out["items"]) == sorted(it.id for it in items)
    rows = await tm.list_trend_items(platforms=["bilibili"], limit=10)
    assert len(rows) == 3
    row = await tm.get_task(tid)
    assert row["status"] == "done"


@pytest.mark.asyncio
async def test_run_radar_pull_records_failure(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    tid = await _seed_breakdown_task(tm, mode="radar_pull", inp={"platforms": ["xhs"]})
    reg = FakeRegistry(fetch_radar_error=VendorNetworkError("net"))
    ctx = _build_ctx(
        tm=tm,
        task_id=tid,
        work_dir=tmp_path / "wd",
        mode="radar_pull",
        inp={"platforms": ["xhs"]},
        registry=reg,
    )
    with pytest.raises(VendorError):
        await pipeline.run_radar_pull(ctx)
    row = await tm.get_task(tid)
    assert row["status"] == "failed"
    assert row["error_kind"] == "network"


# --------------------------------------------------------------------------- #
# compare_accounts                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_compare_accounts_happy_path(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    inp = {
        "account_urls": [
            "https://www.bilibili.com/space/uid/1",
            "https://www.youtube.com/@channel",
        ],
        "max_videos_per_account": 5,
    }
    tid = await _seed_breakdown_task(tm, mode="compare_accounts", inp=inp)
    ds = FakeDashScope(
        chat_responses=[
            ChatResult(
                content="{}",
                model="qwen-max",
                parsed_json={
                    "common_traits": ["fast pacing"],
                    "differentiators": [{"url": "x", "edge": "humour"}],
                    "gaps": ["short-form"],
                    "recommendations": ["try clips"],
                },
            )
        ]
    )

    class _NoVidsRegistry(FakeRegistry):
        def resolve_collector(self, platform: str, *, engine_pref: str = "auto"):  # type: ignore[override]
            from idea_collectors import CollectorChoice

            return CollectorChoice(engine="a", name=f"{platform}-api")

        def _engine_a_for(self, platform: str):  # type: ignore[override]
            class _Stub:
                async def fetch_user(self, url: str, limit: int):  # noqa: A002 — local stub
                    return []

            return _Stub()

    reg = _NoVidsRegistry()
    ctx = _build_ctx(
        tm=tm,
        task_id=tid,
        work_dir=tmp_path / "wd",
        mode="compare_accounts",
        inp=inp,
        dashscope=ds,
        registry=reg,
    )
    out = await pipeline.run_compare_accounts(ctx)
    assert out["analysis"]["common_traits"] == ["fast pacing"]
    assert len(out["accounts"]) == 2
    row = await tm.get_task(tid)
    assert row["status"] == "done"


@pytest.mark.asyncio
async def test_run_compare_accounts_missing_urls_format_error(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    tid = await _seed_breakdown_task(tm, mode="compare_accounts", inp={})
    ctx = _build_ctx(tm=tm, task_id=tid, work_dir=tmp_path / "wd", mode="compare_accounts")
    with pytest.raises(VendorError):
        await pipeline.run_compare_accounts(ctx)
    row = await tm.get_task(tid)
    assert row["error_kind"] == "format"


# --------------------------------------------------------------------------- #
# script_remix                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_script_remix_returns_variants(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    inp = {
        "hook_text": "你不会相信",
        "body_outline": "三段式",
        "target_platform": "douyin",
        "my_brand_keywords": ["效率", "工具"],
        "target_duration_seconds": 60,
        "num_variants": 2,
        "use_mdrm_hints": True,
    }
    tid = await _seed_breakdown_task(tm, mode="script_remix", inp=inp)
    mdrm = FakeMdrm(
        search_results=[
            (
                HookRecord(
                    id="h1",
                    hook_type="悬念",
                    hook_text="爆款",
                    persona=None,
                    platform="douyin",
                    score=0.9,
                ),
                0.92,
            ),
        ]
    )
    ds = FakeDashScope(
        chat_responses=[
            ChatResult(
                content="{}",
                model="qwen-max",
                parsed_json={
                    "variants": [
                        {"title": "v1", "hook_line": "前 3s"},
                        {"title": "v2", "hook_line": "前 3s2"},
                    ]
                },
            )
        ]
    )
    ctx = _build_ctx(
        tm=tm,
        task_id=tid,
        work_dir=tmp_path / "wd",
        mode="script_remix",
        inp=inp,
        dashscope=ds,
        mdrm=mdrm,
    )
    out = await pipeline.run_script_remix(ctx)
    assert len(out["variants"]) == 2
    assert out["mdrm_inspirations"][0]["hook_text"] == "爆款"
    row = await tm.get_task(tid)
    assert row["status"] == "done"


@pytest.mark.asyncio
async def test_run_script_remix_continues_when_mdrm_search_fails(
    tmp_path: Path, task_manager: IdeaTaskManager
) -> None:
    tm = task_manager
    inp = {
        "hook_text": "你好",
        "num_variants": 1,
        "use_mdrm_hints": True,
        "target_platform": "douyin",
    }
    tid = await _seed_breakdown_task(tm, mode="script_remix", inp=inp)

    class _BadMdrm(FakeMdrm):
        async def search_similar_hooks(self, *a: Any, **kw: Any):  # type: ignore[override]
            raise RuntimeError("vector backend cold")

    ds = FakeDashScope(
        chat_responses=[
            ChatResult(content="{}", model="qwen-max", parsed_json={"variants": [{"title": "v"}]}),
        ]
    )
    ctx = _build_ctx(
        tm=tm,
        task_id=tid,
        work_dir=tmp_path / "wd",
        mode="script_remix",
        inp=inp,
        mdrm=_BadMdrm(),
        dashscope=ds,
    )
    out = await pipeline.run_script_remix(ctx)
    assert out["variants"][0]["title"] == "v"
    assert out["mdrm_inspirations"] == []
    row = await tm.get_task(tid)
    assert row["status"] == "done"
