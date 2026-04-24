"""Log incoming/outgoing messages via hooks + JSON lines file."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openakita.plugins.api import PluginAPI, PluginBase


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _serialize_incoming(message: Any) -> dict[str, Any]:
    if message is None:
        return {"kind": "unknown", "note": "no message object"}
    return {
        "kind": "incoming",
        "id": getattr(message, "id", ""),
        "channel": getattr(message, "channel", ""),
        "user_id": getattr(message, "user_id", ""),
        "chat_id": getattr(message, "chat_id", ""),
        "plain_text": (getattr(message, "plain_text", "") or "")[:4000],
    }


def _serialize_outgoing(**kwargs: Any) -> dict[str, Any]:
    """Best-effort payload for hooks that may pass different keyword names."""
    safe: dict[str, Any] = {"kind": "outgoing"}
    for key in ("message", "outgoing", "text", "chat_id", "channel"):
        if key in kwargs:
            val = kwargs[key]
            if hasattr(val, "__dict__") and not isinstance(val, (str, int, float, bool, type(None))):
                safe[key] = repr(val)[:2000]
            else:
                safe[key] = val
    if len(safe) == 1:
        safe["raw_kwargs_keys"] = list(kwargs.keys())
    return safe


class Plugin(PluginBase):
    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._log_path: Path | None = None

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data = api.get_data_dir()
        self._log_path = data / "messages.jsonl"

        api.register_hook("on_message_received", self._on_message_received)
        api.register_hook("on_message_sending", self._on_message_sending)
        api.log(f"message-logger: writing JSON lines to {self._log_path}", "info")

    def on_unload(self) -> None:
        self._api = None

    def _append_line(self, record: dict[str, Any]) -> None:
        if self._api is None or self._log_path is None:
            return
        line = json.dumps(record, ensure_ascii=False) + "\n"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(line)
        self._api.log(f"message-logger: {record.get('direction', 'event')} {record.get('ts', '')}", "info")

    async def _on_message_received(self, message: Any = None, **kwargs: Any) -> None:
        rec = {
            "ts": _utc_iso(),
            "direction": "incoming",
            "payload": _serialize_incoming(message),
        }
        self._append_line(rec)

    async def _on_message_sending(self, **kwargs: Any) -> None:
        rec = {
            "ts": _utc_iso(),
            "direction": "outgoing",
            "payload": _serialize_outgoing(**kwargs),
        }
        self._append_line(rec)

