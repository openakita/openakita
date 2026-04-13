"""Per-chat bot enable/disable configuration store."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openakita.utils.atomic_io import atomic_json_write

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("data/sessions/bot_config.json")


@dataclass
class BotConfigRule:
    channel: str
    chat_id: str
    user_id: str
    enabled: bool
    response_mode: str | None = None


class BotConfigStore:
    """Manage per-chat / per-user bot enable/disable rules.

    Matching priority (first match wins):
      1. Exact  channel + chat_id + user_id
      2. Wildcard  channel + chat_id + user_id="*"
      3. No match -> default True (enabled)
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._path = storage_path or _DEFAULT_PATH
        self._rules: list[BotConfigRule] = []
        self._load()

    def is_enabled(self, channel: str, chat_id: str, user_id: str) -> bool:
        for r in self._rules:
            if r.channel == channel and r.chat_id == chat_id and r.user_id == user_id:
                return r.enabled
        for r in self._rules:
            if r.channel == channel and r.chat_id == chat_id and r.user_id == "*":
                return r.enabled
        return True

    def get_response_mode(self, channel: str, chat_id: str, user_id: str) -> str | None:
        """Return the per-chat response_mode override, or None if not set."""
        for r in self._rules:
            if r.channel == channel and r.chat_id == chat_id and r.user_id == user_id:
                return r.response_mode
        for r in self._rules:
            if r.channel == channel and r.chat_id == chat_id and r.user_id == "*":
                return r.response_mode
        return None

    def set_rule(self, rule: BotConfigRule) -> None:
        for i, r in enumerate(self._rules):
            if (
                r.channel == rule.channel
                and r.chat_id == rule.chat_id
                and r.user_id == rule.user_id
            ):
                self._rules[i] = rule
                self._save()
                return
        self._rules.append(rule)
        self._save()

    def delete_rule(self, channel: str, chat_id: str, user_id: str) -> bool:
        before = len(self._rules)
        self._rules = [
            r
            for r in self._rules
            if not (r.channel == channel and r.chat_id == chat_id and r.user_id == user_id)
        ]
        changed = len(self._rules) != before
        if changed:
            self._save()
        return changed

    def list_rules(self, channel: str | None = None) -> list[dict[str, Any]]:
        rules = self._rules if channel is None else [r for r in self._rules if r.channel == channel]
        return [asdict(r) for r in rules]

    def _load(self) -> None:
        if not self._path.exists():
            self._rules = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._rules = [
                BotConfigRule(
                    channel=item["channel"],
                    chat_id=item["chat_id"],
                    user_id=item.get("user_id", "*"),
                    enabled=item.get("enabled", True),
                    response_mode=item.get("response_mode"),
                )
                for item in data.get("rules", [])
                if isinstance(item, dict) and "channel" in item and "chat_id" in item
            ]
            logger.info(f"[BotConfig] Loaded {len(self._rules)} rule(s) from {self._path}")
        except Exception as e:
            logger.warning(f"[BotConfig] Failed to load {self._path}: {e}")
            self._rules = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self._path, {"rules": [asdict(r) for r in self._rules]})
