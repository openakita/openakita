"""Radar rule preset library — CRUD on top of ``radar_rules_library``.

The preset store is a single JSON-encoded list persisted under the
``radar_rules_library`` config key (``FinpulseTaskManager.get_config`` /
``set_configs``). Keeping the storage logic in one place lets both the
REST routes (``/radar/library``) and the unit tests share the exact
same semantics, avoiding the usual route-vs-helper drift trap.

Contract:

* Presets are ordered newest-first.
* The library caps at :data:`MAX_PRESETS` entries so the config blob
  never grows unbounded; oldest entries fall off at save time.
* Saving a name that already exists **replaces** it (name is the
  primary key). This matches the user expectation of a "Save as"
  dialog that updates in place on matching names.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol


MAX_PRESETS = 50
MAX_NAME_LEN = 64
MAX_RULES_BYTES = 8000


class _ConfigStore(Protocol):
    """The minimal slice of :class:`FinpulseTaskManager` we rely on.

    Keeping this protocol narrow means the unit test can stand up a
     25-line in-memory fake instead of booting an SQLite database.
    """

    async def get_config(self, key: str) -> str: ...
    async def set_configs(self, updates: dict[str, str]) -> None: ...


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_parse(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, Any]] = []
    for it in parsed:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "rules_text": str(it.get("rules_text") or ""),
                "saved_at": str(it.get("saved_at") or ""),
            }
        )
    return out


async def list_presets(store: _ConfigStore) -> list[dict[str, Any]]:
    """Return all saved presets, newest-first."""

    raw = await store.get_config("radar_rules_library")
    return _safe_parse(raw)


async def save_preset(
    store: _ConfigStore,
    *,
    name: str,
    rules_text: str,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Upsert ``name`` → ``rules_text`` into the library.

    Raises :class:`ValueError` on empty/overflowing inputs so callers
    can surface a 400 without swallowing the message.
    """

    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("name is required")
    if len(clean_name) > MAX_NAME_LEN:
        clean_name = clean_name[:MAX_NAME_LEN]
    if not rules_text or not rules_text.strip():
        raise ValueError("rules_text is required")
    if len(rules_text) > MAX_RULES_BYTES:
        raise ValueError(f"rules_text too large (> {MAX_RULES_BYTES} bytes)")

    existing = await list_presets(store)
    kept = [it for it in existing if it.get("name") != clean_name]
    entry = {
        "name": clean_name,
        "rules_text": rules_text,
        "saved_at": now_iso or _utc_now_iso(),
    }
    kept.insert(0, entry)
    kept = kept[:MAX_PRESETS]
    await store.set_configs({"radar_rules_library": json.dumps(kept, ensure_ascii=False)})
    return entry


async def delete_preset(store: _ConfigStore, name: str) -> bool:
    """Remove a preset by name; returns ``True`` iff something was removed."""

    target = (name or "").strip()
    if not target:
        return False
    existing = await list_presets(store)
    remaining = [it for it in existing if it.get("name") != target]
    if len(remaining) == len(existing):
        return False
    await store.set_configs({"radar_rules_library": json.dumps(remaining, ensure_ascii=False)})
    return True


__all__ = [
    "MAX_NAME_LEN",
    "MAX_PRESETS",
    "MAX_RULES_BYTES",
    "delete_preset",
    "list_presets",
    "save_preset",
]
