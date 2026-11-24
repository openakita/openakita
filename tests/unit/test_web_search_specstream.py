from __future__ import annotations

import httpx
import pytest

from openakita.config import settings
from openakita.tools.web_search.base import (
    AuthFailedError,
    MissingCredentialError,
    RateLimitedError,
)
from openakita.tools.web_search.providers.specstream import SpecStreamProvider
from openakita.tools.web_search.registry import get_provider, known_provider_ids


def test_specstream_registered_and_availability(monkeypatch):
    assert "specstream" in known_provider_ids()
    provider = get_provider("specstream")
    assert isinstance(provider, SpecStreamProvider)

    monkeypatch.setattr(settings, "specstream_api_key", "")
    assert provider.is_available() is False
    monkeypatch.setattr(settings, "specstream_api_key", "ss-key")
    assert provider.is_available() is True


@pytest.mark.asyncio
async def test_specstream_search_and_news_search(monkeypatch):
    monkeypatch.setattr(settings, "specstream_api_key", "ss-key")
    captured: list[tuple[dict[str, str], dict[str, object]]] = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            captured.append((dict(headers or {}), dict(json or {})))
            return httpx.Response(
                200,
                json={
                    "sources": [
                        {
                            "title": "Example Result",
                            "url": "https://example.com/a",
                            "snippet": "Fallback snippet",
                            "excerpt": "Extracted passage",
                            "source": "example.com",
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    provider = SpecStreamProvider()

    web_results = await provider.search("python release", max_results=3)
    assert len(web_results) == 1
    assert web_results[0].title == "Example Result"
    assert web_results[0].snippet == "Extracted passage"
    assert captured[0][0]["Authorization"] == "Bearer ss-key"
    assert captured[0][1] == {"q": "python release", "num": 3, "passages": True}

    news_results = await provider.news_search("python release", max_results=2)
    assert news_results is not None and len(news_results) == 1
    assert captured[1][1] == {"q": "python release", "num": 2, "passages": True, "news": True}


@pytest.mark.asyncio
async def test_specstream_errors(monkeypatch):
    provider = SpecStreamProvider()
    monkeypatch.setattr(settings, "specstream_api_key", "")
    with pytest.raises(MissingCredentialError):
        await provider.search("test")

    monkeypatch.setattr(settings, "specstream_api_key", "bad-key")

    for status, exc_cls in [(401, AuthFailedError), (429, RateLimitedError)]:
        class ErrorClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, json=None, _code=status):
                return httpx.Response(_code, text="error")

        monkeypatch.setattr(httpx, "AsyncClient", ErrorClient)
        with pytest.raises(exc_cls):
            await provider.search("test")
