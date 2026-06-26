from __future__ import annotations

import httpx
from fastapi import FastAPI

from openakita.api.routes import inbox as inbox_routes
from openakita.inbox.models import InboxMessage
from openakita.inbox.service import InboxService
from openakita.inbox.store import InboxStore


async def test_inbox_routes_list_detail_and_mark(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("openakita.config.settings.inbox_register_enabled", False)
    service = InboxService(
        data_dir=tmp_path,
        store=InboxStore(tmp_path / "inbox.db"),
        broadcast_fetcher=None,
        api_client=None,
    )
    await service.store.upsert_messages(
        [InboxMessage(id="m1", title="Hello", body_markdown="Body")]
    )
    monkeypatch.setattr(inbox_routes, "get_inbox_service", lambda: service)

    app = FastAPI()
    app.include_router(inbox_routes.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        list_response = await client.get("/api/inbox/messages")
        assert list_response.status_code == 200
        assert list_response.json()["unread_count"] == 1

        detail_response = await client.get("/api/inbox/messages/m1")
        assert detail_response.status_code == 200
        assert detail_response.json()["title"] == "Hello"

        read_response = await client.post("/api/inbox/messages/m1/read")
        assert read_response.status_code == 200
        assert read_response.json()["unread_count"] == 0

        missing_response = await client.get("/api/inbox/messages/missing")
        assert missing_response.status_code == 404
