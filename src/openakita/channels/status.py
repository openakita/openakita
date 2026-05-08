"""Effective IM channel status helpers.

This module keeps the "is this IM channel configured?" decision in one place.
Both legacy .env fields and runtime ``im_bots`` entries are valid config
sources, and runtime adapters can prove a channel is usable even when legacy
fields are empty.
"""

from __future__ import annotations

from typing import Any

_ENV_CHANNELS: dict[str, tuple[str, list[str]]] = {
    "telegram": ("telegram_enabled", ["telegram_bot_token"]),
    "feishu": ("feishu_enabled", ["feishu_app_id", "feishu_app_secret"]),
    "wework": ("wework_enabled", ["wework_corp_id", "wework_token", "wework_encoding_aes_key"]),
    "wework_ws": ("wework_ws_enabled", ["wework_ws_bot_id", "wework_ws_secret"]),
    "dingtalk": ("dingtalk_enabled", ["dingtalk_client_id", "dingtalk_client_secret"]),
    "onebot": ("onebot_enabled", []),
    "qqbot": ("qqbot_enabled", ["qqbot_app_id", "qqbot_app_secret"]),
    "wechat": ("wechat_enabled", ["wechat_token"]),
}

_BOT_REQUIRED_CREDENTIALS: dict[str, list[str]] = {
    "telegram": ["bot_token"],
    "feishu": ["app_id", "app_secret"],
    "wework": ["corp_id", "token", "encoding_aes_key"],
    "wework_ws": ["bot_id", "secret"],
    "dingtalk": ["client_id", "client_secret"],
    "onebot": [],
    "onebot_reverse": [],
    "qqbot": ["app_id", "app_secret"],
    "wechat": ["token"],
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _adapter_items(gateway: Any | None) -> list[tuple[str, Any]]:
    if gateway is None:
        return []
    adapters_dict = getattr(gateway, "_adapters", None) or {}
    if isinstance(adapters_dict, dict):
        return list(adapters_dict.items())
    adapters_list = getattr(gateway, "adapters", []) or []
    return [(getattr(adapter, "channel_name", f"adapter_{i}"), adapter) for i, adapter in enumerate(adapters_list)]


def collect_effective_im_status(settings: Any, gateway: Any | None = None) -> dict[str, Any]:
    """Return normalized IM config/runtime status.

    ``channels`` is the deduplicated effective channel type list. A channel is
    effective when it is configured via .env, configured via ``im_bots``, or
    already visible at runtime through a registered adapter.
    """

    runtime_by_channel: dict[str, dict[str, Any]] = {}
    runtime_by_type: dict[str, list[dict[str, Any]]] = {}
    for adapter_name, adapter in _adapter_items(gateway):
        channel = (
            adapter_name
            or getattr(adapter, "channel_name", None)
            or getattr(adapter, "name", None)
            or "unknown"
        )
        channel_type = getattr(adapter, "channel_type", str(channel).split(":")[0])
        runtime = {
            "channel": channel,
            "type": channel_type,
            "bot_id": getattr(adapter, "bot_id", None) or channel,
            "status": (
                "online"
                if getattr(adapter, "is_running", False) or getattr(adapter, "_running", False)
                else "offline"
            ),
        }
        runtime_by_channel[channel] = runtime
        runtime_by_type.setdefault(channel_type, []).append(runtime)

    details: list[dict[str, Any]] = []

    for channel_type, (enabled_field, required_fields) in _ENV_CHANNELS.items():
        enabled = _truthy(getattr(settings, enabled_field, False))
        missing = [
            field
            for field in required_fields
            if not _present(getattr(settings, field, ""))
        ]
        runtime_entries = runtime_by_type.get(channel_type, [])
        runtime_seen = bool(runtime_entries)
        configured = enabled and not missing
        details.append(
            {
                "type": channel_type,
                "source": "env",
                "id": channel_type,
                "name": channel_type,
                "enabled": enabled,
                "configured": configured,
                "missing": missing,
                "runtime_seen": runtime_seen,
                "runtime_status": _best_runtime_status(runtime_entries),
                "runtime_channels": [entry["channel"] for entry in runtime_entries],
            }
        )

    for bot_cfg in getattr(settings, "im_bots", []) or []:
        if not isinstance(bot_cfg, dict):
            continue
        bot_type = str(bot_cfg.get("type") or "")
        if not bot_type:
            continue
        bot_id = str(bot_cfg.get("id") or "")
        channel_name = f"{bot_type}:{bot_id}" if bot_id else bot_type
        credentials = bot_cfg.get("credentials") if isinstance(bot_cfg.get("credentials"), dict) else {}
        required = _BOT_REQUIRED_CREDENTIALS.get(bot_type, [])
        missing = [key for key in required if not _present(credentials.get(key))]
        enabled = bot_cfg.get("enabled", True) is not False
        runtime_entry = runtime_by_channel.get(channel_name)
        configured = enabled and not missing
        details.append(
            {
                "type": bot_type,
                "source": "im_bots",
                "id": bot_id,
                "name": str(bot_cfg.get("name") or bot_id or bot_type),
                "enabled": enabled,
                "configured": configured,
                "missing": missing,
                "channel": channel_name,
                "runtime_seen": runtime_entry is not None,
                "runtime_status": runtime_entry.get("status") if runtime_entry else "unknown",
            }
        )

    channels = sorted(
        {
            detail["type"]
            for detail in details
            if detail.get("configured") or detail.get("runtime_seen")
        }
    )
    return {"channels": channels, "details": details}


def _best_runtime_status(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "unknown"
    if any(entry.get("status") == "online" for entry in entries):
        return "online"
    if any(entry.get("status") == "offline" for entry in entries):
        return "offline"
    return "unknown"
