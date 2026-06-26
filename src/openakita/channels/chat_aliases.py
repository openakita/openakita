"""Per-chat custom alias store.

Stores user-defined display names for (channel, chat_id) pairs.
Aliases are persisted to a JSON file and loaded on init.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openakita.utils.atomic_io import atomic_json_write

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("data/sessions/chat_aliases.json")


class ChatAliasStore:
    """Manage per-chat custom display names.

    Storage layout: ``{channel: {chat_id: alias_string}}``
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._path = storage_path or _DEFAULT_PATH
        self._data: dict[str, dict[str, str]] = {}
        self._load()

    def get_alias(self, channel: str, chat_id: str) -> str | None:
        return self._data.get(channel, {}).get(chat_id)

    def set_alias(self, channel: str, chat_id: str, alias: str) -> None:
        if channel not in self._data:
            self._data[channel] = {}
        self._data[channel][chat_id] = alias
        self._save()

    def delete_alias(self, channel: str, chat_id: str) -> bool:
        bucket = self._data.get(channel)
        if bucket and chat_id in bucket:
            del bucket[chat_id]
            if not bucket:
                del self._data[channel]
            self._save()
            return True
        return False

    def list_aliases(self, channel: str | None = None) -> dict[str, Any]:
        if channel:
            return dict(self._data.get(channel, {}))
        return {ch: dict(m) for ch, m in self._data.items()}

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._data = {
                ch: {cid: str(a) for cid, a in mapping.items()}
                for ch, mapping in raw.items()
                if isinstance(mapping, dict)
            }
            total = sum(len(m) for m in self._data.values())
            logger.info(f"[ChatAlias] Loaded {total} alias(es) from {self._path}")
        except Exception as e:
            logger.warning(f"[ChatAlias] Failed to load {self._path}: {e}")
            self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self._path, self._data)
