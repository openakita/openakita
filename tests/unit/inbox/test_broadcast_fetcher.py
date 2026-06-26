from __future__ import annotations

import httpx

from openakita.inbox.broadcast_fetcher import BroadcastFetcher
from openakita.inbox.models import ClientContext


class FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "version": 1,
                "generated_at": "2026-01-01T00:00:00+00:00",
                "signature": None,
                "messages": [
                    {
                        "id": "m1",
                        "title": "Windows",
                        "body_markdown": "Body",
                        "target_rule": {"platforms": ["windows"]},
                    },
                    {
                        "id": "m2",
                        "title": "Linux",
                        "body_markdown": "Body",
                        "target_rule": {"platforms": ["linux"]},
                    },
                ],
            },
            request=httpx.Request("GET", url),
        )


async def test_broadcast_fetcher_filters_messages(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    fetcher = BroadcastFetcher(url="https://example.com/inbox/broadcast.json")
    messages = await fetcher.fetch(
        ClientContext(
            install_id_hash="hash",
            version="1.0.0",
            platform="windows",
            channel="release",
        )
    )

    assert [message.id for message in messages] == ["m1"]


async def test_broadcast_fetcher_rejects_bad_document(monkeypatch) -> None:
    class BadClient(FakeAsyncClient):
        async def get(self, url: str) -> httpx.Response:
            return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", BadClient)
    fetcher = BroadcastFetcher(url="https://example.com/inbox/broadcast.json")

    try:
        await fetcher.fetch(ClientContext(install_id_hash="hash"))
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("expected ValueError")
