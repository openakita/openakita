"""Authorization Code + PKCE integration with OpenAkita Account."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from openakita.account.config import (
    DEFAULT_ACCOUNT_BASE_URL,
    DEFAULT_ACCOUNT_CLIENT_ID,
    disabled_credential_usernames,
)
from openakita.account.status_store import AccountStatusStore

logger = logging.getLogger(__name__)

CLIENT_ID = DEFAULT_ACCOUNT_CLIENT_ID
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 1455
CALLBACK_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/auth/callback"


class AccountOIDCError(Exception):
    pass


class TokenStore(Protocol):
    async def load_refresh_token(self) -> str | None: ...

    async def save_refresh_token(self, token: str) -> None: ...

    async def clear(self) -> None: ...


class KeyringTokenStore:
    service = "OpenAkita Account"
    username = "openakita-desktop-refresh-token"

    def __init__(self, *, username: str | None = None) -> None:
        self.username = username or type(self).username

    async def load_refresh_token(self) -> str | None:
        def _load() -> str | None:
            try:
                import keyring
            except ImportError as exc:
                raise AccountOIDCError("OS keyring support is not installed") from exc
            return keyring.get_password(self.service, self.username)

        return await asyncio.to_thread(_load)

    async def save_refresh_token(self, token: str) -> None:
        def _save() -> None:
            try:
                import keyring
            except ImportError as exc:
                raise AccountOIDCError("OS keyring support is not installed") from exc
            keyring.set_password(self.service, self.username, token)

        await asyncio.to_thread(_save)

    async def clear(self) -> None:
        def _clear() -> None:
            try:
                import keyring

                keyring.delete_password(self.service, self.username)
            except Exception:
                return

        await asyncio.to_thread(_clear)


async def clear_disabled_account_credentials() -> None:
    """Remove every locally known account credential slot without reading it."""

    await asyncio.gather(
        *(
            KeyringTokenStore(username=username).clear()
            for username in disabled_credential_usernames()
        )
    )


@dataclass
class LoginAttempt:
    attempt_id: str
    state: str
    verifier: str
    authorization_url: str
    status: str = "pending"
    error: str | None = None
    created_at: float = field(default_factory=time.time)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _preferred_callback_language(accept_language: str) -> str:
    """Choose a supported callback language from an HTTP Accept-Language value."""
    preferences: list[tuple[float, int, str]] = []
    for index, item in enumerate(accept_language.split(",")):
        parts = [part.strip() for part in item.split(";")]
        tag = parts[0].lower()
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.lower().startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        if quality > 0:
            preferences.append((quality, -index, tag))
    for _quality, _index, tag in sorted(preferences, reverse=True):
        if tag == "zh" or tag.startswith("zh-"):
            return "zh"
        if tag == "en" or tag.startswith("en-"):
            return "en"
    return "en"


def _callback_page_html(*, success: bool, language: str) -> bytes:
    copy = {
        "zh": {
            "page_title": "登录成功" if success else "登录失败",
            "eyebrow": "OpenAkita",
            "title": "登录成功" if success else "登录失败",
            "message": (
                "你已成功登录 OpenAkita。"
                if success
                else "登录过程中遇到问题，请返回 OpenAkita 重新登录。"
            ),
            "hint": "现在可以关闭此页面。",
        },
        "en": {
            "page_title": "Signed in successfully" if success else "Sign-in failed",
            "eyebrow": "OpenAkita",
            "title": "Signed in successfully" if success else "Sign-in failed",
            "message": (
                "You’re now signed in to OpenAkita."
                if success
                else "Something went wrong. Return to OpenAkita and try signing in again."
            ),
            "hint": "You can close this tab now.",
        },
    }
    lang = "zh" if language == "zh" else "en"
    text = copy[lang]
    document_language = "zh-CN" if lang == "zh" else "en"
    state_class = "success" if success else "error"
    state_icon = "&#10003;" if success else "!"
    status_role = "status" if success else "alert"
    html = f"""<!doctype html>
<html lang="{document_language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{text["page_title"]} · OpenAkita</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{
      min-height: 100vh; margin: 0; display: grid; place-items: center; padding: 24px;
      color: #182033; background:
        radial-gradient(circle at 50% 0%, rgba(59, 130, 246, .13), transparent 38%),
        linear-gradient(180deg, #f8fafc 0%, #eef2f7 100%);
    }}
    .card {{
      width: min(100%, 440px); padding: 42px 38px 30px; text-align: center;
      border: 1px solid rgba(148, 163, 184, .28); border-radius: 22px;
      background: rgba(255, 255, 255, .92); box-shadow: 0 24px 70px rgba(15, 23, 42, .12);
    }}
    .mark {{
      width: 64px; height: 64px; margin: 0 auto 24px; display: grid; place-items: center;
      border-radius: 20px; font-size: 32px; font-weight: 700;
    }}
    .success .mark {{ color: #047857; background: #d1fae5; box-shadow: 0 0 0 8px rgba(16, 185, 129, .08); }}
    .error .mark {{ color: #b91c1c; background: #fee2e2; box-shadow: 0 0 0 8px rgba(239, 68, 68, .07); }}
    .eyebrow {{ margin: 0 0 8px; color: #2563eb; font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ margin: 0; font-size: 28px; line-height: 1.25; letter-spacing: -.02em; }}
    .message {{ margin: 14px auto 0; max-width: 340px; color: #64748b; font-size: 15px; line-height: 1.65; }}
    .hint {{ margin: 26px 0 0; padding: 12px 14px; border-radius: 12px; color: #475569; background: #f1f5f9; font-size: 13px; }}
    @media (prefers-color-scheme: dark) {{
      body {{ color: #e5e7eb; background: radial-gradient(circle at 50% 0%, rgba(59, 130, 246, .18), transparent 38%), #0f172a; }}
      .card {{ border-color: rgba(148, 163, 184, .2); background: rgba(17, 24, 39, .94); box-shadow: 0 24px 70px rgba(0, 0, 0, .35); }}
      .message {{ color: #94a3b8; }}
      .hint {{ color: #cbd5e1; background: rgba(51, 65, 85, .65); }}
      .success .mark {{ color: #6ee7b7; background: rgba(6, 78, 59, .72); }}
      .error .mark {{ color: #fca5a5; background: rgba(127, 29, 29, .62); }}
    }}
  </style>
</head>
<body>
  <main class="card {state_class}" role="{status_role}" aria-labelledby="callback-title">
    <div class="mark" aria-hidden="true">{state_icon}</div>
    <p class="eyebrow">{text["eyebrow"]}</p>
    <h1 id="callback-title">{text["title"]}</h1>
    <p class="message">{text["message"]}</p>
    <p class="hint">{text["hint"]}</p>
  </main>
</body>
</html>"""
    return html.encode("utf-8")


class AccountOIDCManager:
    def __init__(
        self,
        *,
        store: AccountStatusStore,
        token_store: TokenStore | None = None,
        account_base_url: str | None = None,
        client_id: str | None = None,
    ) -> None:
        self._store = store
        self._tokens = token_store or KeyringTokenStore()
        self._base_url = (
            account_base_url
            or os.environ.get("OPENAKITA_ACCOUNT_BASE_URL", DEFAULT_ACCOUNT_BASE_URL)
        ).rstrip("/")
        self._client_id = (
            client_id or os.environ.get("OPENAKITA_ACCOUNT_CLIENT_ID", DEFAULT_ACCOUNT_CLIENT_ID)
        ).strip()
        if not self._client_id:
            raise ValueError("account client ID must not be empty")
        self._attempts: dict[str, LoginAttempt] = {}
        self._server: asyncio.Server | None = None
        self._access_token: str | None = None
        self._session_id: str | None = None
        self._account_user_id: str | None = None

    async def start(self) -> LoginAttempt:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        attempt_id = secrets.token_urlsafe(18)
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": CALLBACK_URI,
                "response_type": "code",
                "scope": "openid profile email offline_access entitlements organizations",
                "state": state,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        attempt = LoginAttempt(
            attempt_id=attempt_id,
            state=state,
            verifier=verifier,
            authorization_url=f"{self._base_url}/oauth/authorize?{query}",
        )
        self._attempts[attempt_id] = attempt
        self._server = await asyncio.start_server(
            lambda reader, writer: self._callback(reader, writer, attempt),
            CALLBACK_HOST,
            CALLBACK_PORT,
        )
        asyncio.create_task(self._expire_attempt(attempt))
        return attempt

    async def attempt_status(self, attempt_id: str) -> dict:
        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            raise AccountOIDCError("unknown login attempt")
        return {"status": attempt.status, "error": attempt.error}

    async def snapshot(self) -> dict:
        # The identity snapshot is intentionally retained for offline cache and
        # audit purposes. Its presence alone must not make the UI appear signed
        # in after the OS-vault refresh token has been cleared on logout.
        if not await self._tokens.load_refresh_token():
            return {"status": "signed_out"}
        return (await self._store.snapshot()) or {"status": "signed_out"}

    async def refresh_entitlements(self) -> dict:
        access_token = await self._valid_access_token()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self._base_url}/api/v1/me/entitlements",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code != 200:
            raise AccountOIDCError(f"entitlement refresh failed with HTTP {response.status_code}")
        payload = response.json()
        if not self._account_user_id:
            snapshot = await self._store.snapshot()
            self._account_user_id = snapshot.get("account_user_id") if snapshot else None
        if not self._account_user_id:
            raise AccountOIDCError("account identity is unavailable")
        await self._store.save_entitlements(
            account_user_id=self._account_user_id,
            entitlements_json=json.dumps(payload, separators=(",", ":")),
        )
        return payload

    async def logout(self) -> str:
        if self._session_id:
            await self._store.revoke_session(self._session_id)
        await self._tokens.clear()
        self._access_token = None
        self._session_id = None
        self._account_user_id = None
        query = urlencode({"client_id": self._client_id})
        return f"{self._base_url}/oauth/end-session?{query}"

    async def _callback(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        attempt: LoginAttempt,
    ) -> None:
        language = "en"
        try:
            request_line = (await asyncio.wait_for(reader.readline(), timeout=5)).decode(
                "ascii", errors="replace"
            )
            parts = request_line.strip().split(" ")
            if len(parts) != 3 or parts[0] != "GET":
                raise AccountOIDCError("invalid loopback callback")
            accept_language = ""
            for _ in range(64):
                raw_header = await asyncio.wait_for(reader.readline(), timeout=5)
                if raw_header in {b"", b"\r\n", b"\n"}:
                    break
                if len(raw_header) > 8_192:
                    raise AccountOIDCError("loopback callback header is too large")
                header = raw_header.decode("latin-1", errors="replace")
                name, separator, value = header.partition(":")
                if separator and name.strip().lower() == "accept-language":
                    accept_language = value.strip()
            language = _preferred_callback_language(accept_language)
            query = parse_qs(urlsplit(parts[1]).query)
            state = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            if not secrets.compare_digest(state, attempt.state) or not code:
                raise AccountOIDCError("invalid OAuth state or code")
            await self._complete(code=code, verifier=attempt.verifier)
            attempt.status = "complete"
            body = _callback_page_html(success=True, language=language)
            status = b"200 OK"
        except Exception as exc:
            logger.warning("OpenAkita Account login failed: %s", exc)
            attempt.status = "failed"
            attempt.error = str(exc)
            body = _callback_page_html(success=False, language=language)
            status = b"400 Bad Request"
        writer.write(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Type: text/html; charset=utf-8\r\n"
            + f"Content-Language: {'zh-CN' if language == 'zh' else 'en'}\r\n".encode()
            + b"Cache-Control: no-store\r\n"
            + b"X-Content-Type-Options: nosniff\r\n"
            + b"Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _complete(self, *, code: str, verifier: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_response = await client.post(
                f"{self._base_url}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self._client_id,
                    "redirect_uri": CALLBACK_URI,
                    "code_verifier": verifier,
                },
            )
            if token_response.status_code != 200:
                raise AccountOIDCError(
                    f"token exchange failed with HTTP {token_response.status_code}"
                )
            tokens = token_response.json()
            access_token = str(tokens.get("access_token", ""))
            refresh_token = str(tokens.get("refresh_token", ""))
            if not access_token or not refresh_token:
                raise AccountOIDCError("token response is incomplete")
            userinfo_response = await client.get(
                f"{self._base_url}/oauth/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo_response.status_code != 200:
                raise AccountOIDCError("userinfo request failed")
            profile = userinfo_response.json()
        account_user_id = str(profile.get("sub", ""))
        if not account_user_id:
            raise AccountOIDCError("userinfo is missing sub")
        session_id = secrets.token_urlsafe(18)
        await self._tokens.save_refresh_token(refresh_token)
        try:
            await self._store.save_authenticated(
                account_user_id=account_user_id,
                profile_json=json.dumps(profile, separators=(",", ":")),
                session_id=session_id,
            )
        except Exception:
            await self._tokens.clear()
            raise
        self._access_token = access_token
        self._session_id = session_id
        self._account_user_id = account_user_id
        await self.refresh_entitlements()

    async def _valid_access_token(self) -> str:
        if self._session_id and not await self._store.session_is_active(self._session_id):
            self._access_token = None
            raise AccountOIDCError("account is suspended or session was revoked")
        if self._access_token:
            return self._access_token
        snapshot = await self._store.snapshot()
        if snapshot and snapshot.get("status") != "active":
            raise AccountOIDCError("account is suspended")
        refresh_token = await self._tokens.load_refresh_token()
        if not refresh_token:
            raise AccountOIDCError("not signed in")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._client_id,
                },
            )
        if response.status_code != 200:
            await self._tokens.clear()
            raise AccountOIDCError("refresh token is no longer valid")
        tokens = response.json()
        self._access_token = str(tokens.get("access_token", ""))
        rotated = str(tokens.get("refresh_token", ""))
        if rotated:
            await self._tokens.save_refresh_token(rotated)
        if not self._access_token:
            raise AccountOIDCError("refresh response is incomplete")
        return self._access_token

    async def _expire_attempt(self, attempt: LoginAttempt) -> None:
        await asyncio.sleep(180)
        if attempt.status == "pending":
            attempt.status = "expired"
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
                self._server = None
