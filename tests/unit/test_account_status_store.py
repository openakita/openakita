from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from openakita.account.status_store import (
    AccountStatusStore,
    EventConflictError,
    InvalidSignatureError,
    status_signature,
)


def status_body() -> bytes:
    return (
        b'{"event_id":"evt_contract_001","event_type":"account.user.status.changed",'
        b'"occurred_at":"2026-08-06T03:02:03Z","user_id":"usr_contract",'
        b'"status":"suspended","previous_status":"active","reason":"contract test"}\n'
    )


def test_signature_matches_account_contract() -> None:
    assert (
        status_signature("contract-test-secret", "1785985323", "evt_contract_001", status_body())
        == "sha256=291fde8efaab9e999bc0a82946f788976b9bf4bdc939a5d7b05862a65b3c9ec1"
    )


@pytest.mark.asyncio
async def test_status_event_is_idempotent_and_conflicts_on_payload_change(tmp_path) -> None:
    store = AccountStatusStore(tmp_path)
    now = datetime.fromtimestamp(1785985323, tz=UTC)
    signature = status_signature(
        "contract-test-secret", "1785985323", "evt_contract_001", status_body()
    )
    request = {
        "secret": "contract-test-secret",
        "window_seconds": 300,
        "timestamp": "1785985323",
        "event_id": "evt_contract_001",
        "idempotency_key": "evt_contract_001",
        "signature": signature,
        "body": status_body(),
        "now": now,
    }
    first = await store.process(**request)
    assert not first.duplicate
    duplicate = await store.process(**request)
    assert duplicate.duplicate

    changed = json.loads(status_body())
    changed["reason"] = "different"
    changed_body = json.dumps(changed, separators=(",", ":")).encode()
    request.update(
        body=changed_body,
        signature=status_signature(
            "contract-test-secret", "1785985323", "evt_contract_001", changed_body
        ),
    )
    with pytest.raises(EventConflictError):
        await store.process(**request)


@pytest.mark.asyncio
async def test_status_event_rejects_invalid_signature(tmp_path) -> None:
    store = AccountStatusStore(tmp_path)
    with pytest.raises(InvalidSignatureError):
        await store.process(
            secret="contract-test-secret",
            window_seconds=300,
            timestamp="1785985323",
            event_id="evt_contract_001",
            idempotency_key="evt_contract_001",
            signature="sha256=bad",
            body=status_body(),
            now=datetime.fromtimestamp(1785985323, tz=UTC),
        )
