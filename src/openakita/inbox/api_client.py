from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiofiles
import httpx

from .models import ClientContext, InboxMessage


@dataclass
class ClientTokenState:
    client_token: str
    token_id: str
    expires_at: str

    @property
    def needs_renewal(self) -> bool:
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
        except ValueError:
            return True
        return expires <= datetime.now(UTC) + timedelta(days=30)


class InboxApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        token_path: Path,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_path = token_path
        self.timeout_seconds = timeout_seconds

    async def load_token(self) -> ClientTokenState | None:
        try:
            async with aiofiles.open(self.token_path, encoding="utf-8") as f:
                data = json.loads(await f.read())
            return ClientTokenState(
                client_token=str(data["client_token"]),
                token_id=str(data["token_id"]),
                expires_at=str(data["expires_at"]),
            )
        except Exception:
            return None

    async def save_token(self, state: ClientTokenState) -> None:
        await asyncio.to_thread(self.token_path.parent.mkdir, parents=True, exist_ok=True)
        payload = {
            "client_token": state.client_token,
            "token_id": state.token_id,
            "expires_at": state.expires_at,
        }
        async with aiofiles.open(self.token_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(payload, ensure_ascii=False, indent=2))

    async def ensure_token(self, context: ClientContext) -> ClientTokenState | None:
        if not self.base_url:
            return None
        state = await self.load_token()
        if state is None:
            state = await self.register(context)
            await self.save_token(state)
            return state
        if state.needs_renewal:
            try:
                renewed = await self.renew(context, state)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (401, 403, 404):
                    raise
                renewed = await self.register(context)
            await self.save_token(renewed)
            return renewed
        return state

    async def register(self, context: ClientContext) -> ClientTokenState:
        async with self._client() as client:
            challenge_resp = await client.post(f"{self.base_url}/client/auth/challenge")
            challenge_resp.raise_for_status()
            challenge = challenge_resp.json()
            nonce = await asyncio.to_thread(
                solve_pow,
                str(challenge["prefix"]),
                int(challenge["difficulty"]),
            )
            register_resp = await client.post(
                f"{self.base_url}/client/auth/register",
                json={
                    "install_id_hash": context.install_id_hash,
                    "challenge_id": challenge["challenge_id"],
                    "nonce": nonce,
                    "client_version": context.version,
                    "platform": context.platform,
                    "channel": context.channel,
                },
            )
            register_resp.raise_for_status()
            return _token_state(register_resp.json())

    async def renew(self, context: ClientContext, state: ClientTokenState) -> ClientTokenState:
        async with self._client() as client:
            response = await client.post(
                f"{self.base_url}/client/auth/renew",
                headers=_auth_headers(state),
                json={
                    "client_version": context.version,
                    "platform": context.platform,
                    "channel": context.channel,
                },
            )
            response.raise_for_status()
            return _token_state(response.json())

    async def poll(self, context: ClientContext, state: ClientTokenState) -> list[InboxMessage]:
        try:
            return await self._poll_with_state(context, state)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (401, 403, 404):
                raise
            fresh = await self.register(context)
            await self.save_token(fresh)
            return await self._poll_with_state(context, fresh)

    async def _poll_with_state(
        self,
        context: ClientContext,
        state: ClientTokenState,
    ) -> list[InboxMessage]:
        async with self._client() as client:
            response = await client.post(
                f"{self.base_url}/client/inbox/poll",
                headers=_auth_headers(state),
                json={
                    "client_version": context.version,
                    "platform": context.platform,
                    "channel": context.channel,
                },
            )
            response.raise_for_status()
            data = response.json()
        messages = []
        for raw in data.get("messages") or []:
            if isinstance(raw, dict):
                message = InboxMessage.from_payload(raw, source="l1_api")
                if message.id:
                    messages.append(message)
        return messages

    async def ack(self, message_id: str, event: str, state: ClientTokenState) -> None:
        async with self._client() as client:
            response = await client.post(
                f"{self.base_url}/client/inbox/ack",
                headers=_auth_headers(state),
                json={"campaign_id": message_id, "event": event},
            )
            if response.status_code == 404:
                return
            response.raise_for_status()

    async def record_update_event(
        self,
        payload: dict[str, Any],
        state: ClientTokenState,
    ) -> None:
        async with self._client() as client:
            response = await client.post(
                f"{self.base_url}/client/inbox/update-event",
                headers=_auth_headers(state),
                json=payload,
            )
            response.raise_for_status()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True)


def solve_pow(prefix: str, difficulty: int) -> str:
    nonce = 0
    while True:
        candidate = str(nonce)
        if verify_pow(prefix, candidate, difficulty):
            return candidate
        nonce += 1


def verify_pow(prefix: str, nonce: str, difficulty: int) -> bool:
    if difficulty <= 0:
        return True
    digest = hashlib.sha256(f"{prefix}{nonce}".encode()).digest()
    full_bytes, remaining_bits = divmod(difficulty, 8)
    if any(byte != 0 for byte in digest[:full_bytes]):
        return False
    if remaining_bits == 0:
        return True
    mask = 0xFF << (8 - remaining_bits) & 0xFF
    return digest[full_bytes] & mask == 0


def _token_state(data: dict[str, Any]) -> ClientTokenState:
    return ClientTokenState(
        client_token=str(data["client_token"]),
        token_id=str(data["token_id"]),
        expires_at=str(data["expires_at"]),
    )


def _auth_headers(state: ClientTokenState) -> dict[str, str]:
    return {"Authorization": f"Bearer {state.client_token}"}
