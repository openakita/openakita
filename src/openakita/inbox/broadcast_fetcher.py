from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .models import ClientContext, InboxMessage
from .signature import verify_minisign_signature
from .targeting import should_show_message

logger = logging.getLogger(__name__)


class BroadcastFetcher:
    def __init__(
        self,
        *,
        url: str,
        public_key: str = "",
        minisign_executable: str = "minisign",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.url = url
        self.public_key = public_key.strip()
        self.minisign_executable = minisign_executable
        self.timeout_seconds = timeout_seconds

    async def fetch(self, context: ClientContext) -> list[InboxMessage]:
        if not self.url:
            return []
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get(self.url)
            response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise ValueError("Inbox broadcast document must be a JSON object")

        await self._verify_document(document)
        raw_messages = document.get("messages") or []
        if not isinstance(raw_messages, list):
            raise ValueError("Inbox broadcast messages must be a list")

        messages: list[InboxMessage] = []
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            message = InboxMessage.from_payload(raw, source="l0_broadcast")
            if message.id and should_show_message(message, context):
                messages.append(message)
        return messages

    async def _verify_document(self, document: dict[str, Any]) -> None:
        signature = document.get("signature")
        unsigned = dict(document)
        unsigned["signature"] = None
        canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ok = await verify_minisign_signature(
            message=canonical,
            signature=signature if isinstance(signature, str) else None,
            public_key=self.public_key,
            minisign_executable=self.minisign_executable,
        )
        if not ok:
            raise ValueError("Inbox broadcast signature verification failed")
