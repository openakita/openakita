import asyncio
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from openakita.account.oidc import (
    CALLBACK_URI,
    CLIENT_ID,
    DEFAULT_ACCOUNT_BASE_URL,
    AccountOIDCManager,
    KeyringTokenStore,
    LoginAttempt,
    _callback_page_html,
    _preferred_callback_language,
    clear_disabled_account_credentials,
    pkce_challenge,
)


def test_pkce_challenge_rfc7636_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_loopback_contract_constants() -> None:
    assert CLIENT_ID == "openakita-desktop"
    parsed = urlsplit(CALLBACK_URI)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == 1455
    assert parse_qs("state=a&code=b") == {"state": ["a"], "code": ["b"]}


@pytest.mark.parametrize(
    ("accept_language", "expected"),
    [
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh"),
        ("en-US,en;q=0.9,zh;q=0.2", "en"),
        ("fr-FR,fr;q=0.9", "en"),
        ("zh;q=0,en;q=0.8", "en"),
    ],
)
def test_callback_language_follows_browser_preference(
    accept_language: str,
    expected: str,
) -> None:
    assert _preferred_callback_language(accept_language) == expected


def test_callback_page_is_localized_and_handles_both_states() -> None:
    success = _callback_page_html(success=True, language="zh").decode()
    failure = _callback_page_html(success=False, language="en").decode()

    assert '<html lang="zh-CN">' in success
    assert "登录成功" in success
    assert "现在可以关闭此页面" in success
    assert '<main class="card error" role="alert"' in failure
    assert "Sign-in failed" in failure
    assert "Return to OpenAkita and try signing in again" in failure
    assert "127.0.0.1" not in success


class _TokenStore:
    def __init__(self, token: str | None) -> None:
        self.token = token

    async def load_refresh_token(self) -> str | None:
        return self.token

    async def save_refresh_token(self, token: str) -> None:
        self.token = token

    async def clear(self) -> None:
        self.token = None


class _SnapshotStore:
    async def snapshot(self) -> dict:
        return {"status": "active", "account_user_id": "user-1"}


class _CallbackWriter:
    def __init__(self) -> None:
        self.response = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.response.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


@pytest.mark.asyncio
async def test_callback_returns_localized_secure_html() -> None:
    manager = AccountOIDCManager(
        store=_SnapshotStore(),  # type: ignore[arg-type]
        token_store=_TokenStore(None),
    )
    manager._complete = AsyncMock()  # type: ignore[method-assign]
    attempt = LoginAttempt(
        attempt_id="attempt-1",
        state="expected-state",
        verifier="verifier",
        authorization_url="https://account.example/authorize",
    )
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"GET /auth/callback?code=code-1&state=expected-state HTTP/1.1\r\n"
        b"Host: 127.0.0.1:1455\r\n"
        b"Accept-Language: zh-CN,zh;q=0.9,en;q=0.8\r\n\r\n"
    )
    reader.feed_eof()
    writer = _CallbackWriter()

    await manager._callback(reader, writer, attempt)  # type: ignore[arg-type]

    response = bytes(writer.response)
    assert response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"Content-Type: text/html; charset=utf-8\r\n" in response
    assert b"Content-Language: zh-CN\r\n" in response
    assert b"Content-Security-Policy: default-src 'none'" in response
    assert "你已成功登录 OpenAkita。".encode() in response
    assert attempt.status == "complete"
    assert writer.closed is True


def test_account_base_url_defaults_to_hosted_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAKITA_ACCOUNT_BASE_URL", raising=False)
    manager = AccountOIDCManager(
        store=_SnapshotStore(),  # type: ignore[arg-type]
        token_store=_TokenStore(None),
    )

    assert manager._base_url == DEFAULT_ACCOUNT_BASE_URL


@pytest.mark.asyncio
async def test_manager_uses_configured_client_id_in_provider_requests() -> None:
    manager = AccountOIDCManager(
        store=_SnapshotStore(),  # type: ignore[arg-type]
        token_store=_TokenStore(None),
        account_base_url="https://accounts.vendor.example",
        client_id="vendor-desktop",
    )

    logout_url = await manager.logout()

    assert logout_url == (
        "https://accounts.vendor.example/oauth/end-session?client_id=vendor-desktop"
    )


@pytest.mark.asyncio
async def test_disabled_mode_clears_known_credential_slots(monkeypatch) -> None:
    cleared: list[str] = []

    async def fake_clear(self: KeyringTokenStore) -> None:
        cleared.append(self.username)

    monkeypatch.setattr(
        "openakita.account.oidc.disabled_credential_usernames",
        lambda: {"openakita-desktop-refresh-token", "vendor-desktop-refresh-token"},
    )
    monkeypatch.setattr(KeyringTokenStore, "clear", fake_clear)

    await clear_disabled_account_credentials()

    assert sorted(cleared) == [
        "openakita-desktop-refresh-token",
        "vendor-desktop-refresh-token",
    ]


@pytest.mark.asyncio
async def test_snapshot_requires_refresh_token_even_when_offline_cache_exists() -> None:
    manager = AccountOIDCManager(
        store=_SnapshotStore(),  # type: ignore[arg-type]
        token_store=_TokenStore(None),
    )

    assert await manager.snapshot() == {"status": "signed_out"}
