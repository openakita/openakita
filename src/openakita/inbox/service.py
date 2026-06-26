from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openakita import __version__
from openakita.config import settings

from .api_client import ClientTokenState, InboxApiClient
from .broadcast_fetcher import BroadcastFetcher
from .install_id import get_or_create_install_id_hash
from .models import ClientContext, InboxMessage, utc_now_iso
from .store import InboxStore
from .update_handler import find_update_available

logger = logging.getLogger(__name__)


@dataclass
class RefreshResult:
    ok: bool
    fetched_l0: int = 0
    fetched_l1: int = 0
    stored: int = 0
    errors: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "fetched_l0": self.fetched_l0,
            "fetched_l1": self.fetched_l1,
            "stored": self.stored,
            "errors": self.errors or [],
        }


class InboxService:
    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        broadcast_fetcher: BroadcastFetcher | None = None,
        api_client: InboxApiClient | None = None,
        store: InboxStore | None = None,
    ) -> None:
        self.data_dir = data_dir or (settings.data_dir / "inbox")
        self.store = store or InboxStore(self.data_dir / "inbox.db")
        self.broadcast_fetcher = broadcast_fetcher or BroadcastFetcher(
            url=settings.inbox_broadcast_url,
            public_key=settings.inbox_minisign_public_key,
            minisign_executable=settings.inbox_minisign_executable,
        )
        self.api_client = api_client or InboxApiClient(
            base_url=settings.inbox_api_url,
            token_path=self.data_dir / "client_token.json",
        )
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_refresh_at: str | None = None
        self._last_error: str | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_l0_count = 0
        self._last_l1_count = 0

    async def start(self) -> None:
        if not settings.inbox_enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="openakita-inbox-refresh")
        logger.info("[Inbox] background refresh loop started")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
        logger.info("[Inbox] background refresh loop stopped")

    async def refresh(self) -> dict[str, Any]:
        if not settings.inbox_enabled:
            result = RefreshResult(ok=False, errors=["inbox disabled"])
            self._last_result = result.as_dict()
            return self._last_result

        errors: list[str] = []
        before_ids = {message["id"] for message in await self.store.list_messages()}
        before_unread = await self.store.unread_count()
        context = await self.context()

        l0_messages: list[InboxMessage] = []
        try:
            l0_messages = await self.broadcast_fetcher.fetch(context)
        except Exception as exc:
            errors.append(f"l0: {exc}")
            logger.warning("[Inbox] L0 broadcast refresh failed: %s", exc)

        l1_messages: list[InboxMessage] = []
        token_state: ClientTokenState | None = None
        if settings.inbox_register_enabled and settings.inbox_api_url:
            try:
                token_state = await self.api_client.ensure_token(context)
                if token_state is not None:
                    l1_messages = await self.api_client.poll(context, token_state)
            except Exception as exc:
                errors.append(f"l1: {exc}")
                logger.warning("[Inbox] L1 API refresh failed: %s", exc)

        messages = _dedupe_messages([*l0_messages, *l1_messages])
        await self.store.upsert_messages(messages)

        after_messages = await self.store.list_messages()
        after_ids = {message["id"] for message in after_messages}
        after_unread = await self.store.unread_count()
        self._last_refresh_at = utc_now_iso()
        self._last_error = "; ".join(errors) if errors else None
        self._last_l0_count = len(l0_messages)
        self._last_l1_count = len(l1_messages)

        new_ids = after_ids - before_ids
        for message in after_messages:
            if message["id"] in new_ids:
                _fire_event(
                    "inbox:new_message",
                    {
                        "id": message["id"],
                        "title": message["title"],
                        "priority": message["priority"],
                    },
                )

        if after_unread != before_unread:
            _fire_event("inbox:unread_changed", {"unread_count": after_unread})

        update_payload = find_update_available(after_messages, current_version=__version__)
        if update_payload is not None:
            _fire_event("inbox:update_available", update_payload)

        result = RefreshResult(
            ok=not errors,
            fetched_l0=len(l0_messages),
            fetched_l1=len(l1_messages),
            stored=len(messages),
            errors=errors,
        )
        self._last_result = result.as_dict()
        return self._last_result

    async def list_messages(self, *, include_dismissed: bool = False) -> list[dict[str, Any]]:
        return await self.store.list_messages(include_dismissed=include_dismissed)

    async def get_message(self, message_id: str) -> dict[str, Any] | None:
        return await self.store.get_message(message_id)

    async def unread_count(self) -> int:
        return await self.store.unread_count()

    async def mark_event(self, message_id: str, event: str) -> bool:
        changed = await self.store.mark_event(message_id, event)
        if not changed:
            return False
        unread = await self.store.unread_count()
        _fire_event("inbox:unread_changed", {"unread_count": unread})
        if settings.inbox_enabled and settings.inbox_register_enabled:
            asyncio.create_task(self._ack_remote(message_id, event))
        return True

    async def record_update_event(self, payload: dict[str, Any]) -> bool:
        if (
            not settings.inbox_enabled
            or not settings.inbox_register_enabled
            or not settings.telemetry_enabled
        ):
            return False
        try:
            context = await self.context()
            token_state = await self.api_client.ensure_token(context)
            if token_state is None:
                return False
            event_payload = {
                "from_version": payload.get("from_version") or __version__,
                "to_version": payload.get("to_version") or payload.get("version") or "",
                "platform": payload.get("platform") or context.platform or "",
                "channel": payload.get("channel") or context.channel or "",
                "event_type": payload.get("event_type") or payload.get("event") or "offered",
                "update_plan_id": payload.get("update_plan_id"),
                "detail": payload.get("detail"),
            }
            await self.api_client.record_update_event(event_payload, token_state)
            return True
        except Exception as exc:
            logger.debug("[Inbox] update event upload failed: %s", exc)
            return False

    async def diagnostics(self) -> dict[str, Any]:
        token = await self.api_client.load_token()
        context = await self.context()
        return {
            "enabled": settings.inbox_enabled,
            "register_enabled": settings.inbox_register_enabled,
            "broadcast_url": settings.inbox_broadcast_url,
            "api_url": settings.inbox_api_url,
            "poll_interval_sec": settings.inbox_poll_interval_sec,
            "signature_verification": bool(settings.inbox_minisign_public_key),
            "data_dir": str(self.data_dir),
            "install_id_hash": context.install_id_hash,
            "client_version": context.version,
            "platform": context.platform,
            "channel": context.channel,
            "last_refresh_at": self._last_refresh_at,
            "last_error": self._last_error,
            "last_result": self._last_result,
            "last_l0_count": self._last_l0_count,
            "last_l1_count": self._last_l1_count,
            "token": {
                "present": token is not None,
                "token_id": token.token_id if token else None,
                "expires_at": token.expires_at if token else None,
                "needs_renewal": token.needs_renewal if token else None,
            },
        }

    async def context(self) -> ClientContext:
        install_id_hash = await get_or_create_install_id_hash(self.data_dir)
        return ClientContext(
            install_id_hash=install_id_hash,
            version=__version__,
            platform=_platform_name(),
            channel=settings.inbox_channel,
        )

    async def _ack_remote(self, message_id: str, event: str) -> None:
        try:
            context = await self.context()
            token_state = await self.api_client.ensure_token(context)
            if token_state is not None:
                await self.api_client.ack(message_id, event, token_state)
        except Exception as exc:
            logger.debug("[Inbox] remote ack failed for %s/%s: %s", message_id, event, exc)

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.refresh()
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("[Inbox] refresh loop iteration failed: %s", exc)
            interval = max(60, int(settings.inbox_poll_interval_sec or 1800))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except TimeoutError:
                continue


_service: InboxService | None = None


def get_inbox_service() -> InboxService:
    global _service
    if _service is None:
        _service = InboxService()
    return _service


def reset_inbox_service() -> None:
    global _service
    _service = None


def _dedupe_messages(messages: list[InboxMessage]) -> list[InboxMessage]:
    merged: dict[str, InboxMessage] = {}
    for message in messages:
        if message.id:
            merged[message.id] = message
    return list(merged.values())


def _platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def _fire_event(event: str, payload: dict[str, Any]) -> None:
    try:
        from openakita.api.routes.websocket import fire_event

        fire_event(event, payload)
    except Exception as exc:
        logger.debug("[Inbox] websocket event dropped: %s", exc)
