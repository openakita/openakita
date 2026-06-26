from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .models import ClientContext, InboxMessage


def should_show_message(message: InboxMessage, context: ClientContext) -> bool:
    return (
        _is_active_window(message)
        and match_target_rule(message.target_rule, context)
        and is_in_rollout(
            message.id,
            context.install_id_hash,
            message.rollout_percent,
        )
    )


def match_target_rule(rule: Mapping[str, Any] | None, context: ClientContext) -> bool:
    if not rule:
        return True
    return (
        _match_install_id(rule, context.install_id_hash)
        and _match_string_set(rule, context.platform, include_keys=("platforms", "platform"))
        and _match_string_set(rule, context.channel, include_keys=("channels", "channel"))
        and _match_version(rule, context.version)
    )


def is_in_rollout(scope_id: str, install_id_hash: str, percent: int) -> bool:
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    digest = hashlib.sha256(f"{scope_id}:{install_id_hash}".encode()).hexdigest()
    return int(digest[:8], 16) % 100 < percent


def _match_install_id(rule: Mapping[str, Any], install_id_hash: str) -> bool:
    excluded = _string_values(
        rule,
        "exclude_install_id_hashes",
        "excluded_install_id_hashes",
        "exclude_install_ids",
        "excluded_install_ids",
    )
    if install_id_hash in excluded:
        return False
    included = _string_values(
        rule, "install_id_hash", "install_id_hashes", "install_id", "install_ids"
    )
    return not included or install_id_hash in included


def _match_string_set(
    rule: Mapping[str, Any],
    value: str | None,
    *,
    include_keys: tuple[str, ...],
) -> bool:
    include = {_normalize_label(item) for item in _string_values(rule, *include_keys)}
    exclude = {
        _normalize_label(item)
        for item in _string_values(
            rule,
            *(f"exclude_{key}" for key in include_keys),
            *(f"excluded_{key}" for key in include_keys),
        )
    }
    normalized = _normalize_label(value)
    if normalized and normalized in exclude:
        return False
    if include and normalized not in include:
        return False
    return True


def _match_version(rule: Mapping[str, Any], version: str | None) -> bool:
    normalized = _normalize_version_label(version)
    included = {
        _normalize_version_label(item)
        for item in _string_values(rule, "versions", "client_versions", "version")
    }
    excluded = {
        _normalize_version_label(item)
        for item in _string_values(rule, "exclude_versions", "excluded_versions")
    }
    if normalized and normalized in excluded:
        return False
    if included and normalized not in included:
        return False

    min_version = _first_string(rule, "min_client_version", "min_version")
    max_version = _first_string(rule, "max_client_version", "max_version")
    if (min_version or max_version) and not normalized:
        return False
    if min_version and _compare_versions(normalized, min_version) < 0:
        return False
    if max_version and _compare_versions(normalized, max_version) > 0:
        return False
    return True


def _compare_versions(left: str | None, right: str | None) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    width = max(len(left_parts), len(right_parts))
    padded_left = left_parts + (0,) * (width - len(left_parts))
    padded_right = right_parts + (0,) * (width - len(right_parts))
    if padded_left == padded_right:
        return 0
    return 1 if padded_left > padded_right else -1


def _version_parts(value: str | None) -> tuple[int, ...]:
    normalized = _normalize_version_label(value)
    if not normalized:
        return ()
    return tuple(int(part) for part in re.findall(r"\d+", normalized))


def _normalize_version_label(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return text[1:] if text.startswith("v") else text


def _normalize_label(value: str | None) -> str:
    return value.strip().lower() if value else ""


def _is_active_window(message: InboxMessage) -> bool:
    now = datetime.now(UTC)
    publish_at = _parse_datetime(message.publish_at)
    expire_at = _parse_datetime(message.expire_at)
    if publish_at is not None and publish_at > now:
        return False
    return not (expire_at is not None and expire_at < now)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _first_string(rule: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = rule.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _string_values(rule: Mapping[str, Any], *keys: str) -> set[str]:
    values: set[str] = set()
    for key in keys:
        raw = rule.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            if raw.strip():
                values.add(raw.strip())
            continue
        if isinstance(raw, Sequence) and not isinstance(raw, bytes | bytearray):
            values.update(str(item).strip() for item in raw if str(item).strip())
    return values
