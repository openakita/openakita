from __future__ import annotations

import re
from typing import Any


def compare_versions(left: str | None, right: str | None) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    width = max(len(left_parts), len(right_parts))
    padded_left = left_parts + (0,) * (width - len(left_parts))
    padded_right = right_parts + (0,) * (width - len(right_parts))
    if padded_left == padded_right:
        return 0
    return 1 if padded_left > padded_right else -1


def update_payload_for_message(
    message: dict[str, Any],
    *,
    current_version: str | None,
) -> dict[str, Any] | None:
    if str(message.get("type") or "").lower() != "update":
        return None

    raw = message.get("raw")
    if not isinstance(raw, dict):
        raw = {}
    cta = message.get("cta")
    if not isinstance(cta, dict):
        cta = {}

    target_version = _first_text(
        raw.get("target_version"),
        raw.get("to_version"),
        raw.get("version"),
        cta.get("target_version"),
        cta.get("version"),
    )
    manifest_url = _first_text(
        raw.get("manifest_url"),
        raw.get("manifest"),
        raw.get("update_manifest_url"),
        cta.get("manifest_url"),
        cta.get("url"),
    )
    if (
        target_version
        and current_version
        and compare_versions(current_version, target_version) >= 0
    ):
        return None

    min_supported_version = _first_text(
        raw.get("min_supported_version"),
        raw.get("minimum_supported_version"),
        cta.get("min_supported_version"),
    )
    force_upgrade = bool(raw.get("force_upgrade") or raw.get("force") or cta.get("force_upgrade"))
    forced_now = bool(
        min_supported_version
        and current_version
        and compare_versions(current_version, min_supported_version) < 0
    )

    return {
        "message_id": message.get("id"),
        "title": message.get("title"),
        "version": target_version,
        "manifest_url": manifest_url,
        "force_upgrade": force_upgrade,
        "min_supported_version": min_supported_version,
        "policy": "forced_now"
        if forced_now
        else ("forced_after_delay" if force_upgrade else "prompt"),
    }


def find_update_available(
    messages: list[dict[str, Any]],
    *,
    current_version: str | None,
) -> dict[str, Any] | None:
    for message in messages:
        payload = update_payload_for_message(message, current_version=current_version)
        if payload is not None:
            return payload
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _version_parts(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    normalized = str(value).strip().lower()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    return tuple(int(part) for part in re.findall(r"\d+", normalized))
