"""Unit tests for the Keenable web_search provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.tools.web_search import AuthFailedError, RateLimitedError
from openakita.tools.web_search.providers import keenable as kn
from openakita.tools.web_search.registry import get_provider


def test_registered_as_keyless_provider() -> None:
    p = get_provider("keenable")
    assert p.label == "Keenable"
    assert p.requires_credential is False
    assert p.is_available() is True
    assert 40 < p.auto_detect_order < 100  # after jina, before duckduckgo


def test_build_request_keyless_uses_public_endpoint() -> None:
    url, headers, body = kn.build_request("hello", 5, "")
    assert url == "https://api.keenable.ai/v1/search/public"
    assert headers["X-Keenable-Title"] == "OpenAkita"
    assert "X-API-Key" not in headers
    assert body == {"query": "hello", "max_results": 5}


def test_build_request_with_key_uses_keyed_endpoint() -> None:
    url, headers, body = kn.build_request("hello", 0, "kn-secret")
    assert url == "https://api.keenable.ai/v1/search"
    assert headers["X-API-Key"] == "kn-secret"
    assert "X-Keenable-Title" not in headers
    assert body["max_results"] == 1


def test_parse_results_maps_and_drops_rows_without_url() -> None:
    data = {
        "results": [
            {"title": "A", "url": "https://a.example", "description": "desc a"},
            {"title": "no url"},
            {"title": "B", "url": "https://b.example", "snippet": "snip b"},
            {"title": "C", "url": "https://c.example"},
        ]
    }
    out = kn.parse_results(data, 2)
    assert [r.url for r in out] == ["https://a.example", "https://b.example"]
    assert out[0].snippet == "desc a"
    assert out[1].snippet == "snip b"
    assert out[0].source == "keenable"
    assert kn.parse_results({"oops": 1}, 5) == []
    assert kn.parse_results(None, 5) == []


def _fake_client(status: int, payload: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = "x"
    resp.json.return_value = payload or {}
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


@pytest.mark.asyncio
async def test_search_posts_to_public_endpoint_without_key() -> None:
    cm, client = _fake_client(200, {"results": [{"title": "T", "url": "https://t.example"}]})
    with (
        patch("httpx.AsyncClient", return_value=cm),
        patch.object(kn.settings, "keenable_api_key", ""),
    ):
        out = await kn.KeenableProvider().search("q", max_results=3)
    assert [r.title for r in out] == ["T"]
    args, kwargs = client.post.call_args
    assert args[0].endswith("/v1/search/public")
    assert kwargs["json"] == {"query": "q", "max_results": 3}


@pytest.mark.asyncio
async def test_search_error_mapping() -> None:
    for status, err in ((401, AuthFailedError), (429, RateLimitedError)):
        cm, _ = _fake_client(status)
        with patch("httpx.AsyncClient", return_value=cm), pytest.raises(err):
            await kn.KeenableProvider().search("q")
