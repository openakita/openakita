from __future__ import annotations

from openakita.inbox.models import InboxMessage
from openakita.inbox.store import InboxStore


async def test_store_upsert_list_mark_and_unread_count(tmp_path) -> None:
    store = InboxStore(tmp_path / "inbox.db")
    await store.upsert_messages(
        [
            InboxMessage(
                id="m1",
                title="First",
                body_markdown="Body",
                priority="high",
                cta={"label": "Open", "url": "https://example.com"},
            ),
            InboxMessage(id="m2", title="Second", body_markdown="Body"),
        ]
    )

    messages = await store.list_messages()
    assert [message["id"] for message in messages] == ["m1", "m2"]
    assert await store.unread_count() == 2

    assert await store.mark_event("m1", "read") is True
    assert await store.unread_count() == 1
    message = await store.get_message("m1")
    assert message is not None
    assert message["read_at"] is not None
    assert message["unread"] is False

    assert await store.mark_event("m2", "dismissed") is True
    assert [message["id"] for message in await store.list_messages()] == ["m1"]
    assert len(await store.list_messages(include_dismissed=True)) == 2


async def test_store_upsert_preserves_read_state(tmp_path) -> None:
    store = InboxStore(tmp_path / "inbox.db")
    await store.upsert_messages([InboxMessage(id="m1", title="Old", body_markdown="Body")])
    assert await store.mark_event("m1", "read") is True

    await store.upsert_messages([InboxMessage(id="m1", title="New", body_markdown="Updated")])
    message = await store.get_message("m1")
    assert message is not None
    assert message["title"] == "New"
    assert message["read_at"] is not None
