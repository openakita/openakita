from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from openakita.inbox.api_client import ClientTokenState, InboxApiClient
from openakita.inbox.client_token import solve_pow, verify_pow
from openakita.inbox.models import ClientContext


def test_pow_solver_finds_valid_nonce() -> None:
    nonce = solve_pow("prefix", 8)
    assert verify_pow("prefix", nonce, 8) is True


def test_pow_zero_difficulty_passes() -> None:
    assert verify_pow("prefix", "anything", 0) is True


async def test_ensure_token_re_registers_when_renew_token_is_rejected(tmp_path) -> None:
    client = InboxApiClient(base_url="https://admin.example", token_path=tmp_path / "token.json")
    old = ClientTokenState(
        client_token="old",
        token_id="old-id",
        expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    new = ClientTokenState(
        client_token="new",
        token_id="new-id",
        expires_at=(datetime.now(UTC) + timedelta(days=365)).isoformat(),
    )
    await client.save_token(old)

    async def reject_renew(context, state):
        raise httpx.HTTPStatusError(
            "rejected",
            request=httpx.Request("POST", "https://admin.example/client/auth/renew"),
            response=httpx.Response(401),
        )

    async def register_again(context):
        return new

    client.renew = reject_renew
    client.register = register_again

    state = await client.ensure_token(ClientContext(install_id_hash="hash" * 8))

    assert state == new
    assert (await client.load_token()) == new
