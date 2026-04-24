"""CollectorRegistry / Normalizer / Ranker tests (§6.4 + §6.5)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest
from idea_collectors import (
    API_SAFE_PLATFORMS,
    CollectorRegistry,
    Normalizer,
    Ranker,
)
from idea_engine_crawler import (
    CookiesVault,
    PageResponse,
    PlaywrightDriver,
)
from idea_models import TrendItem
from idea_research_inline.vendor_client import VendorError

# ---------------------------------------------------------------------------\n# Normalizer
# ---------------------------------------------------------------------------\n


def test_normalizer_dedupes_by_external_id():
    n = Normalizer()
    a = TrendItem(
        id="1",
        platform="bilibili",
        external_id="BV1",
        external_url="x",
    )
    b = TrendItem(
        id="2",
        platform="bilibili",
        external_id="BV1",
        external_url="x",
    )
    c = TrendItem(
        id="3",
        platform="bilibili",
        external_id="BV2",
        external_url="x",
    )
    out = n.dedupe([a, b, c])
    assert [it.id for it in out] == ["1", "3"]


def test_normalizer_guesses_hook_type():
    n = Normalizer()
    item = TrendItem(
        id="1",
        platform="bilibili",
        external_id="x",
        external_url="x",
        title="揭秘 AI 编辑器内幕",
    )
    n.annotate([item])
    assert item.hook_type_guess == "悬念"


# ---------------------------------------------------------------------------\n# Ranker
# ---------------------------------------------------------------------------\n


def test_ranker_score_orders_items_descending():
    now = int(time.time())
    big = TrendItem(
        id="big",
        platform="bilibili",
        external_id="b1",
        external_url="x",
        title="AI",
        like_count=10_000,
        comment_count=500,
        view_count=200_000,
        publish_at=now - 3600,
        fetched_at=now,
    )
    small = TrendItem(
        id="small",
        platform="bilibili",
        external_id="b2",
        external_url="x",
        title="AI",
        like_count=1,
        comment_count=0,
        view_count=10_000,
        publish_at=now - 3600,
        fetched_at=now,
    )
    ranked = Ranker().score([small, big], ["AI"])
    assert ranked[0].id == "big"
    assert ranked[1].id == "small"


def test_ranker_annotate_mdrm_tolerates_search_failure():
    async def boom(*_a, **_kw):
        raise RuntimeError("mdrm down")

    item = TrendItem(
        id="x",
        platform="bilibili",
        external_id="b1",
        external_url="x",
        title="AI",
    )
    ranker = Ranker(mdrm_search=boom)
    out = asyncio.run(ranker.annotate_mdrm([item]))
    assert out[0].mdrm_hits == []  # no crash


def test_ranker_annotate_mdrm_records_hit_ids():
    async def hits(query, *, limit, min_similarity):
        return [({"id": "h1"}, 0.92), ({"id": "h2"}, 0.85)]

    item = TrendItem(
        id="x",
        platform="bilibili",
        external_id="b1",
        external_url="x",
        title="AI",
    )
    out = asyncio.run(Ranker(mdrm_search=hits).annotate_mdrm([item]))
    assert out[0].mdrm_hits == ["h1", "h2"]


# ---------------------------------------------------------------------------\n# CollectorRegistry
# ---------------------------------------------------------------------------\n


def _httpx_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))


def test_registry_resolves_engine_a_for_safe_platforms(tmp_path: Path):
    async def go():
        async with _httpx_client() as client:
            reg = CollectorRegistry(http_client=client)
            for p in API_SAFE_PLATFORMS:
                choice = reg.resolve_collector(p)
                assert choice.engine == "a"

    asyncio.run(go())


def test_registry_falls_back_to_rss_for_unknown_engine_a(tmp_path: Path):
    async def go():
        async with _httpx_client() as client:
            reg = CollectorRegistry(http_client=client)
            choice = reg.resolve_collector("douyin", engine_pref="a")
            assert choice.engine == "a"
            assert choice.name == "rsshub"

    asyncio.run(go())


def test_registry_engine_b_requires_setup(tmp_path: Path):
    async def go():
        async with _httpx_client() as client:
            reg = CollectorRegistry(http_client=client)
            with pytest.raises(VendorError) as ei:
                reg.resolve_collector("douyin", engine_pref="b")
            assert ei.value.error_kind == "auth"

    asyncio.run(go())


def test_registry_auto_chooses_engine_b_when_enabled(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")

    async def fake_fetch(*, url: str, **kwargs):
        return PageResponse(url=url, status=200, html="", json_payloads=[])

    driver = PlaywrightDriver(override_fetch=fake_fetch)

    async def go():
        async with _httpx_client() as client:
            reg = CollectorRegistry(
                http_client=client,
                vault=vault,
                playwright_driver=driver,
                engine_b_enabled=True,
                risk_acknowledged=True,
            )
            choice = reg.resolve_collector("douyin")  # auto + B available
            assert choice.engine == "b"
            choice2 = reg.resolve_collector("bilibili")  # safe wins
            assert choice2.engine == "a"

    asyncio.run(go())


def test_registry_fetch_for_radar_aggregates_errors(tmp_path: Path):
    bili_payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "bvid": "BV1",
                    "title": "AI 编辑器",
                    "stat": {"like": 10, "view": 100, "reply": 1, "share": 0},
                    "owner": {"name": "alice"},
                    "pubdate": int(time.time()) - 60,
                }
            ]
        },
    }

    def handler(request):
        if "popular" in str(request.url):
            return httpx.Response(200, json=bili_payload)
        if "googleapis" in str(request.url):
            return httpx.Response(403, text="quota")
        return httpx.Response(200, json={})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reg = CollectorRegistry(http_client=client, api_keys={"youtube": "KEY"})
            out = await reg.fetch_for_radar(
                ["bilibili", "youtube"], ["AI"], time_window="30d", limit=5
            )
            ids = [it.external_id for it in out["items"]]
            assert "BV1" in ids
            err_kinds = {e["error_kind"] for e in out["errors"]}
            assert err_kinds & {"auth", "quota", "network"}

    asyncio.run(go())
